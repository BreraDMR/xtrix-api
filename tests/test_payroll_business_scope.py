from datetime import datetime,timedelta
import json
import pytest

def ok(r):
    assert r.status_code in (200,201),r.text
    return r.json()


def business(client,make_user,headers_for,label):
    owner=make_user('business_owner',email=label.lower()+'.owner@example.com')
    h=headers_for(owner)
    b=ok(client.post('/businesses/',json={'name':label,'business_type':'salon'},headers=h))
    person=ok(client.post('/clients/',json={'full_name':label+' Guest','contact_email':label.lower()+'.guest@example.com'},headers=h))
    return {'owner':owner,'h':h,'business':b,'person':person}


def service(client,s,staff,name,price):
    return ok(client.post('/services/',json={'name':name,'duration_minutes':30,'price':price,'specialist_id':staff},headers=s['h']))


def chain(client,s,staff,price,start,completed=True,used=None):
    own=service(client,s,s['owner'].id,'Owner step',50)
    emp=service(client,s,staff,'Worker step',price)
    body={'client_id':s['person']['id'],'specialist_id':s['owner'].id,'date_start':start.isoformat(),'items':[{'service_id':own['id'],'specialist_id':s['owner'].id},{'service_id':emp['id'],'specialist_id':staff},{'service_id':emp['id'],'specialist_id':staff}],'status':'completed' if completed else 'planned','payment_method':'cash'}
    if used:body['used_products']=used
    return ok(client.post('/visits/',json=body,headers=s['h']))


@pytest.fixture
def history(client,db,make_user,headers_for):
    from app.api.crud.crud_employee import create_employee
    from app.schemas.employee import EmployeeCreate
    from app.db.models.user import User
    a=business(client,make_user,headers_for,'FinanceA')
    b=business(client,make_user,headers_for,'FinanceB')
    old=ok(client.post('/employees/create-with-user',json={'full_name':'Historical transferred worker','email':'history.worker@example.com','commission_percentage':10},headers=a['h']))
    staff=old['employee_user_id']
    start=datetime(2026,10,22,9)
    before=chain(client,a,staff,100,start)
    ok(client.delete(f"/employees/{old['id']}",headers=a['h']))
    db.expire_all()
    worker=db.get(User,staff)
    assert worker.business_id is None and worker.employer_id is None
    # Current HTTP blocks transferring by id. The legacy HTTP before35249c6 used this same domain method.
    denied=client.post('/employees/',json={'employee_user_id':staff,'commission_percentage':10},headers=b['h'])
    assert denied.status_code==403,denied.text
    hired=create_employee(db,EmployeeCreate(employee_user_id=staff,commission_percentage=10),business_owner_id=b['owner'].id)
    db.expire_all()
    assert db.get(User,staff).business_id==b['business']['id']
    after=chain(client,b,staff,300,start+timedelta(days=1))
    from app.db.models.visit import Visit
    db.expire_all()
    assert db.get(Visit,before['id']).business_id==a['business']['id']
    assert db.get(Visit,after['id']).business_id==b['business']['id']
    return a,b,old,hired.id,staff,before,after


def salary(client,s,employee):
    return ok(client.post(f'/employees/{employee}/salary/calculate',json={'period_start':'2026-10-01T00:00:00Z','period_end':'2026-11-01T00:00:00Z'},headers=s['h']))


def test_new_employer_cannot_sum_previous_business_history(client,history):
    a,b,old,new,staff,before,after=history
    result=salary(client,b,new)
    print('NEW_EMPLOYER_SALARY',json.dumps({'old_business':a['business']['id'],'current_business':b['business']['id'],'worker':staff,'salary':result}))
    assert result['total_visits']==1
    assert result['total_revenue']==pytest.approx(600)
    assert result['commission_amount']==pytest.approx(60)


def test_previous_employer_reactivated_record_cannot_include_new_business(client,history):
    a,b,old,new,staff,before,after=history
    restored=client.put(f"/employees/{old['id']}",json={'is_active':True},headers=a['h'])
    assert restored.status_code==200,restored.text
    result=salary(client,a,old['id'])
    print('OLD_EMPLOYER_SALARY',json.dumps({'old_business':a['business']['id'],'current_business':b['business']['id'],'worker':staff,'salary':result}))
    assert result['total_visits']==1
    assert result['total_revenue']==pytest.approx(200)
    assert result['commission_amount']==pytest.approx(20)


def test_multi_materials_and_loyalty_count_once_for_normal_repeat(client,db,make_user,headers_for):
    from app.db.models.loyalty_program import LoyaltyTransaction
    from app.db.models.product_usage import ProductUsage
    from app.db.models.product import Product
    s=business(client,make_user,headers_for,'SingleAccounting')
    employee=ok(client.post('/employees/create-with-user',json={'full_name':'Count once worker','email':'count.worker@example.com'},headers=s['h']))
    program=ok(client.post('/loyalty/program',json={'business_id':s['business']['id'],'program_type':'points','points_per_currency':1},headers=s['h']))
    product=ok(client.post('/products/',json={'name':'Counting material','quantity':10,'price_per_unit':1,'business_id':s['business']['id']},headers=s['h']))
    v=chain(client,s,employee['employee_user_id'],100,datetime(2026,10,24,10),completed=False,used=[{'product_id':product['id'],'quantity':2}])
    for _ in range(2):
        ok(client.put(f"/visits/{v['id']}",json={'status':'completed','payment_method':'cash'},headers=s['h']))
    db.expire_all()
    rows=db.query(LoyaltyTransaction).filter(LoyaltyTransaction.visit_id==v['id'],LoyaltyTransaction.transaction_type=='earned').all()
    usage=db.query(ProductUsage).filter(ProductUsage.visit_id==v['id']).all()
    assert len(rows)==1 and rows[0].points_amount==pytest.approx(250)
    assert rows[0].business_id==s['business']['id']
    assert len(usage)==1 and usage[0].quantity==2 and usage[0].business_id==s['business']['id']
    assert db.get(Product,product['id']).quantity==8
