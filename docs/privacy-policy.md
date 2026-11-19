# Xtrix visitor privacy policy

Applies to the Xtrix visitor account: the Xtrix client app and the visitor area on the web.
It does not cover the salon CRM used by salon staff. Last updated: 19 November 2026.

Every number and data item below matches the current code. `tests/test_privacy_policy_doc.py`
fails when a visitor table is added or a quoted lifetime changes, so this file has to move with the code.

## English

### Who is responsible
Xtrix is run by Alexandr Krajuškin, IČO 24334995, Praha 4, Czech Republic. He is the controller of the
data in your visitor account.

Your bookings (visits, services, prices, the client card with your name and phone) are records of the salon
you booked with. The salon is the controller of those records, Xtrix only stores and shows them on the salon's
behalf. Questions about them go to the salon first.

### What we store
For your visitor account:
- **Account** (`visitor_accounts`): your email address, when it was confirmed, a password hash (scrypt, never
  the password itself) and when the account was created.
- **Google sign-in link** (`visitor_google_identities`), only if you connect Google on the web: the Google
  account identifier (subject) and when it was linked. We do not get access to your mailbox, contacts or files.
- **Sign-in sessions** (`visitor_sessions`): a hash of the session token, when it was created, when it expires
  and whether you signed out. The token itself is never stored on our side.
- **One-time links** (`visitor_challenges`): for sign-up, password reset, email change and Google sign-in.
  We keep a hash of the link, its purpose, the email address it was sent to (for email links), the web address
  that asked for it (for Google sign-in) and its timestamps.
- **Link delivery queue** (`visitor_proof_mail_jobs`): one row per email with a link — purpose, language,
  site address, delivery status and attempt count. The link inside is encrypted while it waits to be sent
  and is erased as soon as the email is sent, captured or expires.
- **Access to salon records** (`visitor_grants`): which salon, client card and visit your account may see.
- **Claim and invitation links** (`visitor_claims`): a hash of the link, the salon, client card and visit it
  points to, the invited email address (for salon invitations) and when it was used and by which account.
- **Phones for visit reminders** (`visitor_push_tokens`), only if you allow notifications in the app: the
  phone's Expo push address, its platform (iOS or Android), the app language and the sign-in session it belongs
  to. A reminder waiting in the queue keeps the same push address, the salon name, the service and the visit
  time.

What you see in the app from the salon: salon name, address, phone, email and time zone; visit times, status,
services, staff names and prices.

For security, failed sign-ins are counted per pair of a SHA-256 fingerprint of the email address and your IP
address, and per email fingerprint alone. These counters live only in server memory or our Redis cache.

Our hosting provider keeps technical request logs (IP address, time, requested address) for the period its
service sets.

On your phone the app keeps your session token, the chosen language and, for at most one day, a claim link
you opened but have not confirmed yet. They are stored in the iOS Keychain. The app has no ads, no analytics
and no tracking. If you allow notifications, the app sends the phone's push address to us for visit
reminders; you can switch them off in the phone settings at any time.

### Why
- To create and run your account, show the visits the salon shared with you and let you cancel or reschedule
  them, and, if you allow notifications, remind you of upcoming visits on your phone (performance of the
  service you asked for, GDPR Art. 6(1)(b)).
- To protect accounts against password guessing and abuse (legitimate interest, Art. 6(1)(f)).

We do not sell your data and do not use it for advertising.

### Who processes it for us
- Render (API server, Frankfurt region), Neon (PostgreSQL database), Vercel (web app).
- An email provider that delivers our links: an SMTP server or Google Gmail API, depending on the setup.
- Google, only when you choose to sign in with Google on the web.
- Expo (650 Industries, USA), only if you allow notifications: it gets the push address and the reminder text
  and hands them to Apple or Google, who deliver the notification to your phone.

### How long
- Your account and everything linked to it stays until you delete the account.
- A sign-in session works for 30 days, unless you sign out earlier.
- A phone stays registered for reminders while its sign-in session is active. Signing out, resetting the
  password or deleting the account removes it, and its push address is erased from the reminder queue.
- Email links work for 30 minutes, Google sign-in links for 5 minutes. A salon invitation link works
  for 30 minutes.
- Failed sign-in counters are kept for 30 minutes per email and IP pair, and for one hour per email.
- There is no automatic cleanup yet: expired or revoked sessions and used links stay in the database until you
  delete your account. A sign-up link requested for an address that never finished sign-up currently has no
  set deletion date.

### Deleting your account
You can delete the account yourself in the app: Profile → Delete account. For safety you need to have signed in
within the last 5 minutes. Deletion happens in one step and removes:
- the account, password hash and Google link;
- all sign-in sessions, so every device is signed out;
- the phones registered for reminders, and their push address in the reminder queue;
- your access to salon records;
- all one-time links of the account and sign-up links for its email address, together with their emails
  still waiting in the delivery queue.

What stays:
- The salon's own records (client card, visits, prices). They belong to the salon. Ask the salon if you want
  them changed or removed. Deleting the account does not cancel your bookings.
- Used claim and invitation links keep only their hash and the time of use, so they can never be used again.
  The invited email address is replaced with a placeholder and the link to your account is removed.

### Your rights
You can ask for access to your data, correction, deletion, restriction, portability, or object to processing.
You can change your email address in the app (Profile) and delete the account there. You can also complain to
the Czech data protection office, Úřad pro ochranu osobních údajů (uoou.gov.cz).

### Contact
Alexandr Krajuškin, IČO 24334995 — alexkrayushkin@outlook.com

## Česky

### Kdo odpovídá
Xtrix provozuje Alexandr Krajuškin, IČO 24334995, Praha 4, Česká republika. Je správcem údajů ve vašem
návštěvnickém účtu.

Vaše rezervace (návštěvy, služby, ceny, karta klienta s vaším jménem a telefonem) jsou záznamy salonu,
u kterého jste se objednali. Správcem těchto záznamů je salon, Xtrix je pro salon jen ukládá a zobrazuje.
S dotazy k nim se obracejte nejdřív na salon.

### Co ukládáme
K vašemu návštěvnickému účtu:
- **Účet** (`visitor_accounts`): vaši e-mailovou adresu, kdy byla potvrzena, otisk hesla (scrypt, nikdy ne
  samotné heslo) a kdy byl účet vytvořen.
- **Propojení s Google** (`visitor_google_identities`), jen pokud Google připojíte na webu: identifikátor účtu
  Google (subject) a kdy bylo propojení vytvořeno. K vaší poště, kontaktům ani souborům přístup nemáme.
- **Přihlášení** (`visitor_sessions`): otisk tokenu relace, kdy relace vznikla, kdy vyprší a zda jste se
  odhlásili. Samotný token u nás uložený není.
- **Jednorázové odkazy** (`visitor_challenges`): pro registraci, obnovu hesla, změnu e-mailu a přihlášení
  přes Google. Ukládáme otisk odkazu, jeho účel, e-mail, na který byl odeslán (u e-mailových odkazů), webovou
  adresu, která o něj požádala (u přihlášení přes Google), a časové údaje.
- **Fronta odesílání odkazů** (`visitor_proof_mail_jobs`): jeden řádek za každý e-mail s odkazem — účel, jazyk,
  adresa webu, stav doručení a počet pokusů. Odkaz je během čekání na odeslání zašifrovaný a smaže se, jakmile
  je e-mail odeslán, zachycen nebo odkaz vyprší.
- **Přístup k záznamům salonu** (`visitor_grants`): který salon, kartu klienta a návštěvu smí váš účet vidět.
- **Odkazy pro připojení a pozvánky** (`visitor_claims`): otisk odkazu, salon, karta klienta a návštěva, na
  které odkazuje, pozvaný e-mail (u pozvánek od salonu) a kdy a kterým účtem byl použit.
- **Telefony pro připomínky návštěv** (`visitor_push_tokens`), jen pokud v aplikaci povolíte oznámení: push
  adresa telefonu od Expo, platforma (iOS nebo Android), jazyk aplikace a přihlášení, ke kterému telefon patří.
  Připomínka čekající ve frontě obsahuje stejnou push adresu, název salonu, službu a čas návštěvy.

Co v aplikaci uvidíte od salonu: název, adresu, telefon, e-mail a časové pásmo salonu; čas, stav, služby,
jména pracovníků a ceny návštěv.

Kvůli bezpečnosti počítáme neúspěšná přihlášení pro dvojici otisku SHA-256 e-mailové adresy a vaší IP adresy
a zvlášť pro samotný otisk e-mailu. Tato počítadla jsou jen v paměti serveru nebo v naší mezipaměti Redis.

Náš poskytovatel hostingu uchovává technické záznamy požadavků (IP adresa, čas, požadovaná adresa) po dobu,
kterou stanoví jeho služba.

V telefonu aplikace ukládá váš token přihlášení, zvolený jazyk a nejvýše na jeden den odkaz pro připojení,
který jste otevřeli, ale ještě nepotvrdili. Vše je v klíčence iOS (Keychain). Aplikace nemá reklamy,
analytiku ani sledování. Pokud povolíte oznámení, aplikace nám pošle push adresu telefonu kvůli připomínkám
návštěv; v nastavení telefonu je můžete kdykoli vypnout.

### Proč
- Abychom mohli vytvořit a provozovat váš účet, zobrazit návštěvy, které s vámi salon sdílí, a umožnit vám je
  zrušit nebo přesunout, a pokud povolíte oznámení, připomenout vám nadcházející návštěvy v telefonu (plnění
  služby, o kterou jste požádali, čl. 6 odst. 1 písm. b) GDPR).
- Abychom chránili účty před hádáním hesel a zneužitím (oprávněný zájem, čl. 6 odst. 1 písm. f) GDPR).

Vaše údaje neprodáváme a nepoužíváme je k reklamě.

### Kdo je pro nás zpracovává
- Render (server API, region Frankfurt), Neon (databáze PostgreSQL), Vercel (webová aplikace).
- Poskytovatel e-mailu, který doručuje naše odkazy: server SMTP nebo Google Gmail API podle nastavení.
- Google, jen pokud se na webu rozhodnete přihlásit přes Google.
- Expo (650 Industries, USA), jen pokud povolíte oznámení: dostane push adresu a text připomínky a předá je
  společnosti Apple nebo Google, které oznámení doručí do telefonu.

### Jak dlouho
- Účet a vše, co je s ním spojené, uchováváme, dokud účet nesmažete.
- Přihlášení platí 30 dní, pokud se neodhlásíte dřív.
- Telefon zůstává přihlášen k připomínkám, dokud platí jeho přihlášení. Odhlášení, obnova hesla nebo smazání
  účtu ho odstraní a jeho push adresa se smaže i z fronty připomínek.
- E-mailové odkazy platí 30 minut, odkazy pro přihlášení přes Google 5 minut. Pozvánka od salonu platí
  30 minut.
- Počítadla neúspěšných přihlášení se drží 30 minut pro dvojici e-mailu a IP adresy a jednu hodinu pro e-mail.
- Automatické mazání zatím nemáme: prošlá nebo zrušená přihlášení a použité odkazy zůstávají v databázi, dokud
  účet nesmažete. Odkaz pro registraci adresy, která registraci nedokončila, zatím nemá stanovené datum smazání.

### Smazání účtu
Účet můžete smazat sami v aplikaci: Profil → Smazat účet. Z bezpečnostních důvodů se musíte přihlásit během
posledních 5 minut. Smazání proběhne najednou a odstraní:
- účet, otisk hesla a propojení s Google;
- všechna přihlášení, takže budete odhlášeni na všech zařízeních;
- telefony přihlášené k připomínkám a jejich push adresu ve frontě připomínek;
- váš přístup k záznamům salonu;
- všechny jednorázové odkazy účtu a odkazy pro registraci jeho e-mailu, včetně jejich e-mailů, které ještě
  čekají ve frontě na odeslání.

Co zůstane:
- Vlastní záznamy salonu (karta klienta, návštěvy, ceny). Patří salonu. Pokud je chcete změnit nebo smazat,
  obraťte se na salon. Smazání účtu vaše rezervace nezruší.
- U použitých odkazů pro připojení a pozvánek zůstane jen jejich otisk a čas použití, aby je nešlo použít znovu.
  Pozvaný e-mail se nahradí zástupnou hodnotou a vazba na váš účet se odstraní.

### Vaše práva
Můžete požádat o přístup ke svým údajům, jejich opravu, výmaz, omezení zpracování, přenositelnost nebo vznést
námitku proti zpracování. E-mail můžete změnit v aplikaci (Profil) a tam také účet smazat. Stížnost můžete podat
u Úřadu pro ochranu osobních údajů (uoou.gov.cz).

### Kontakt
Alexandr Krajuškin, IČO 24334995 — alexkrayushkin@outlook.com
