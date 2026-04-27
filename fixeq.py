f='services/v3/analytics.py'
c=open(f,encoding='utf-8').read()
c=c.replace('equity = cash + total_realized','total_market_value = 0.0\nequity = cash + total_realized')
c=c.replace('total_unrealized += unrealized','total_unrealized += unrealized\n            total_market_value += qty * current_price')
c=c.replace('equity += total_unrealized','equity += total_market_value')
open(f,'w',encoding='utf-8').write(c)
print('EQUITY FIX OK')
