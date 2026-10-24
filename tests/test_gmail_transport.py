"""Independent scratch checks. Every credential and HTTP response here is synthetic."""
import base64
from datetime import datetime,timezone,timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import importlib
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
import requests


class FakeCredentials:
    instances=[]
    refreshes=0
    refresh_error=None

    def __init__(self,token=None,refresh_token=None,token_uri=None,client_id=None,client_secret=None,scopes=None,**kwargs):
        self.token=token
        self.scopes=scopes
        self.refresh_token=refresh_token
        self.token_uri=token_uri
        self.client_id=client_id
        self.client_secret=client_secret
        self.expiry=datetime.now(timezone.utc)-timedelta(hours=1)
        self.__class__.instances.append(self)

    @property
    def valid(self):
        return bool(self.token)

    @property
    def expired(self):
        return not self.token

    @classmethod
    def from_authorized_user_info(cls,info,scopes=None):
        return cls(token=info.get('token'),refresh_token=info.get('refresh_token'),token_uri=info.get('token_uri'),client_id=info.get('client_id'),client_secret=info.get('client_secret'),scopes=scopes or info.get('scopes'))

    def refresh(self,request):
        self.__class__.refreshes+=1
        if self.__class__.refresh_error:
            raise self.__class__.refresh_error
        self.token='synthetic-access-token-DO-NOT-LOG'
        self.expiry=datetime.now(timezone.utc)+timedelta(hours=1)


class Response:
    status_code=200
    body={'id':'synthetic-gmail-id-1'}
    text='PRIVATE_RAW_RESPONSE_SHOULD_NOT_BE_LOGGED'

    def json(self):
        if isinstance(self.body,Exception): raise self.body
        return self.body


@pytest.fixture
def setup(monkeypatch):
    module=importlib.import_module('app.api.core.gmail_transport')
    from app.api.core.notification_transport import OutboundMessage, SmtpEmailTransport
    from app.api.core.config import settings
    FakeCredentials.instances=[]
    FakeCredentials.refreshes=0
    FakeCredentials.refresh_error=None
    monkeypatch.setattr(module,'Credentials',FakeCredentials)
    values=dict(email_provider='gmail',gmail_from='sender@example.com',gmail_oauth_client_id='synthetic-client-id',gmail_oauth_client_secret=SecretStr('synthetic-client-secret-DO-NOT-LOG'),gmail_refresh_token=SecretStr('synthetic-refresh-token-DO-NOT-LOG'),gmail_delivery_enabled=True,gmail_allowed_recipients='guest@example.com')
    for key,value in values.items(): monkeypatch.setattr(settings,key,value)
    calls=[]
    reply=Response()
    reply.status_code=200
    reply.body={'id':'synthetic-gmail-id-1'}
    def no_network(*args,**kwargs): raise AssertionError('UNMOCKED NETWORK IS FORBIDDEN')
    monkeypatch.setattr(requests.sessions.Session,'request',no_network)
    monkeypatch.setattr(SmtpEmailTransport,'send',no_network)
    def post(url,*args,**kwargs):
        calls.append({'url':url,'args':args,'kwargs':kwargs})
        if getattr(reply,'exception',None): raise reply.exception
        return reply
    monkeypatch.setattr(requests,'post',post)
    def message(**changes):
        values=dict(channel='email',recipient='guest@example.com',text='Plain text',subject='Subject',html='<p>HTML</p>')
        values.update(changes)
        return OutboundMessage(**values)
    return SimpleNamespace(module=module,settings=settings,calls=calls,reply=reply,message=message)


def send(setup,**changes):
    return setup.module.GmailEmailTransport().send(setup.message(**changes))


def test_disabled_returns_skipped_without_refresh_or_http(setup,monkeypatch):
    monkeypatch.setattr(setup.settings,'gmail_delivery_enabled',False)
    result=send(setup)
    assert result.status=='skipped'
    assert not setup.calls and not FakeCredentials.refreshes


@pytest.mark.parametrize('allowlist',['','  ','other@example.com','guest@example.com.evil','prefixguest@example.com'])
def test_missing_or_nonexact_allowlist_is_deny_all(setup,monkeypatch,allowlist):
    monkeypatch.setattr(setup.settings,'gmail_allowed_recipients',allowlist)
    result=send(setup)
    assert result.status=='skipped'
    assert not setup.calls and not FakeCredentials.refreshes


@pytest.mark.parametrize('channel',['sms','whatsapp'])
def test_nonemail_channel_never_uses_network(setup,channel):
    result=send(setup,channel=channel)
    assert result.status=='skipped'
    assert not setup.calls and not FakeCredentials.refreshes


@pytest.mark.parametrize('allowlist',['*',' another@example.com, guest@example.com '])
def test_explicit_wildcard_or_exact_member_allows_sending(setup,monkeypatch,allowlist):
    monkeypatch.setattr(setup.settings,'gmail_allowed_recipients',allowlist)
    result=send(setup)
    assert result.status=='sent' and result.provider=='gmail'
    assert result.provider_message_id=='synthetic-gmail-id-1'
    assert len(setup.calls)==1
    assert FakeCredentials.refreshes==1
    assert set(FakeCredentials.instances[0].scopes)=={'https://www.googleapis.com/auth/gmail.send'}


@pytest.mark.parametrize('subject,text,html',[
 ('Проверка Xtrix','Здравствуйте, Дамир! Ёжик.','<p>Здравствуйте, Дамир! Ёжик.</p>'),
 ('Kontrola Xtrix','Dobrý den, příliš žluťoučký kůň.','<p>Dobrý den, příliš žluťoučký kůň.</p>'),
 ('Xtrix check','Hello, Damir!','<p>Hello, Damir!</p>'),
])
def test_mime_utf8_from_to_and_https_timeout(setup,subject,text,html):
    result=send(setup,subject=subject,text=text,html=html)
    assert result.status=='sent'
    call=setup.calls[0]
    assert call['url']=='https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
    assert call['kwargs']['timeout']==15
    assert call['kwargs']['headers']['Authorization']=='Bearer synthetic-access-token-DO-NOT-LOG'
    raw=call['kwargs']['json']['raw']
    data=base64.urlsafe_b64decode(raw+'='*((-len(raw))%4))
    msg=BytesParser(policy=policy.default).parsebytes(data)
    assert str(msg['Subject'])==subject
    assert parseaddr(msg['From'])[1]=='sender@example.com'
    assert parseaddr(msg['To'])[1]=='guest@example.com'
    assert not msg['Cc'] and not msg['Bcc']
    assert msg.get_body(preferencelist=('plain',)).get_content().rstrip('\n')==text
    assert msg.get_body(preferencelist=('html',)).get_content().rstrip('\n')==html


@pytest.mark.parametrize('code',[400,401,403,429])
def test_known_http_refusal_never_retries_post_or_logs_response(setup,code,caplog):
    setup.reply.status_code=code
    result=send(setup)
    assert result.status=='failed' and result.error==f'gmail_http:{code}'
    assert len(setup.calls)==1
    assert 'PRIVATE_RAW_RESPONSE_SHOULD_NOT_BE_LOGGED' not in caplog.text
    assert 'synthetic-access-token-DO-NOT-LOG' not in caplog.text
    assert 'synthetic-client-secret-DO-NOT-LOG' not in caplog.text
    assert 'synthetic-refresh-token-DO-NOT-LOG' not in caplog.text


@pytest.mark.parametrize('exception',[requests.Timeout('PRIVATE timeout'),requests.ConnectionError('PRIVATE connection')])
def test_unknown_network_outcome_is_uncertain_without_retry(setup,exception,caplog):
    setup.reply.exception=exception
    result=send(setup)
    assert result.status=='failed' and result.error=='gmail_delivery_uncertain'
    assert len(setup.calls)==1
    assert 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('body',[{}, {'id':''}, {'id':None}, ValueError('PRIVATE raw body')])
def test_accepted_but_missing_message_id_is_uncertain(setup,body,caplog):
    setup.reply.body=body
    result=send(setup)
    assert result.status=='failed' and result.error=='gmail_delivery_uncertain'
    assert len(setup.calls)==1
    assert 'PRIVATE' not in caplog.text


def test_refresh_failure_prevents_send_and_does_not_log_secrets(setup,caplog):
    from google.auth.exceptions import RefreshError
    FakeCredentials.refresh_error=RefreshError('PRIVATE synthetic-refresh-token-DO-NOT-LOG')
    result=send(setup)
    assert result.status=='failed'
    assert not setup.calls
    assert not result.delivered
    assert 'PRIVATE' not in caplog.text
    assert 'synthetic-refresh-token-DO-NOT-LOG' not in caplog.text


@pytest.mark.parametrize('field',['gmail_from','gmail_oauth_client_id','gmail_oauth_client_secret','gmail_refresh_token'])
def test_incomplete_config_never_refreshes_or_posts(setup,monkeypatch,field):
    monkeypatch.setattr(setup.settings,field,SecretStr('') if field.endswith(('secret','token')) else '')
    result=send(setup)
    assert result.status in ('failed','skipped')
    assert not setup.calls and not FakeCredentials.refreshes


@pytest.mark.parametrize('field',['recipient','subject','from'])
def test_crlf_header_injection_cannot_send(setup,monkeypatch,field):
    monkeypatch.setattr(setup.settings,'gmail_allowed_recipients','*')
    if field=='from':
        monkeypatch.setattr(setup.settings,'gmail_from','sender@example.com\r\nBcc: attacker@example.com')
        result=send(setup)
    else:
        result=send(setup,**{field:('guest@example.com' if field=='recipient' else 'Subject')+'\r\nBcc: attacker@example.com'})
    assert result.status in ('failed','skipped')
    assert not setup.calls


@pytest.mark.parametrize('code',[500,502,503])
def test_server_error_is_uncertain_without_retry(setup,code):
    setup.reply.status_code=code
    result=send(setup)
    assert result.status=='failed' and result.error=='gmail_delivery_uncertain'
    assert len(setup.calls)==1
