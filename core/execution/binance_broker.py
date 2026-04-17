import logging, os, hashlib, hmac, time
import requests
logger = logging.getLogger(__name__)

class BinanceBroker:
    BASE_URL = "https://api.binance.com"
    TESTNET_URL = "https://testnet.binance.vision"
    def __init__(self, api_key=None, api_secret=None, testnet=True):
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY","")
        self.api_secret = api_secret or os.environ.get("BINANCE_API_SECRET","")
        self.testnet = testnet
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        if not self.api_key: logger.warning("BinanceBroker: no API credentials")

    def _sign(self, params):
        q = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
        params["signature"] = hmac.new(self.api_secret.encode(),q.encode(),hashlib.sha256).hexdigest()
        return params

    def _headers(self): return {"X-MBX-APIKEY": self.api_key}

    def create_order(self, symbol, side, quantity, order_type="MARKET", price=None):
        params = {"symbol":symbol.upper(),"side":side.upper(),"type":order_type.upper(),"quantity":quantity,"timestamp":int(time.time()*1000)}
        if order_type.upper()=="LIMIT" and price: params["price"]=price; params["timeInForce"]="GTC"
        if not self.api_key: return {"error":"API credentials not configured"}
        try:
            r = requests.post(f"{self.base_url}/api/v3/order",params=self._sign(params),headers=self._headers(),timeout=10)
            r.raise_for_status(); result=r.json(); logger.info("Order: %s %s qty=%.6f orderId=%s",side,symbol,quantity,result.get("orderId")); return result
        except Exception as exc: logger.error("Order failed: %s",exc); return {"error":str(exc)}

    def get_account(self):
        params = {"timestamp":int(time.time()*1000)}
        if not self.api_key: return {"error":"API credentials not configured"}
        try:
            r = requests.get(f"{self.base_url}/api/v3/account",params=self._sign(params),headers=self._headers(),timeout=10)
            r.raise_for_status(); return r.json()
        except Exception as exc: return {"error":str(exc)}