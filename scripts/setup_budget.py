"""Idempotent project-filtered $10 monthly budget; notifications, not a spending cap."""
import json
from pathlib import Path
import requests
from cloud_support import token,PROJECT

BILLING='01D6D3-A3C0D6-DA3A36'

def apply():
    root=Path(__file__).resolve().parents[1]
    body={'displayName':'RidePulse monthly USD 10','budgetFilter':{'projects':['projects/42502534899'],'calendarPeriod':'MONTH','creditTypesTreatment':'EXCLUDE_ALL_CREDITS'},
          'amount':{'specifiedAmount':{'currencyCode':'USD','units':'10'}},
          'thresholdRules':[{'thresholdPercent':v,'spendBasis':'CURRENT_SPEND'} for v in (.5,.9,1.0)],
          'notificationsRule':{'disableDefaultIamRecipients':False,'enableProjectLevelRecipients':True}}
    target=root/'infra/monitoring/budget.json';target.write_text(json.dumps(body,indent=2)+'\n')
    s=requests.Session();s.headers.update({'Authorization':'Bearer '+token(),'x-goog-user-project':PROJECT})
    base=f'https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING}/budgets'
    listing=s.get(base,timeout=30)
    if not listing.ok:r=listing
    else:
        old=next((b for b in listing.json().get('budgets',[]) if b['displayName']==body['displayName']),None)
        if old:body['name']=old['name']
        r=s.request('PATCH' if old else 'POST','https://billingbudgets.googleapis.com/v1/'+old['name'] if old else base,json=body,timeout=30)
    result={'status':r.status_code,'body':r.json()}
    (root/'work/budget-final-setup.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':apply()
