import json,tempfile,unittest
from pathlib import Path
import requests
from uploader.xiaohongshu_uploader import protocol

def response(status,payload):
    r=requests.Response()
    r.status_code=status
    r._content=json.dumps(payload).encode()
    r.url='https://ark.xiaohongshu.com/query'
    return r

def goods(goods_id='sku-1'):
    return {'success':True,'data':{'data':[{'goods_info':{'goods_id':goods_id,'name':'麦芽糖','goods_seller_type':'GOODS_SELLER'}}]}}

class Session:
    def __init__(self,replies):
        self.replies=list(replies)
        self.cookies=requests.cookies.RequestsCookieJar()
        self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append({'url':url,'body':json.loads(kwargs['data']),'cookies':dict(kwargs['cookies']),'headers':dict(kwargs['headers'])})
        status,payload,added=self.replies.pop(0)
        self.cookies.update(added)
        return response(status,payload)

class CreatorSsoTests(unittest.TestCase):
    def client(self,replies):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            p.write_text(json.dumps({'cookies':[{'name':'a1','value':'1'*52},{'name':'web_session','value':'main-session'}]}))
            return protocol.XhsCreatorProtocol(p,session=Session(replies))
    def test_main_session_is_exchanged_once_and_both_goods_stay_bound(self):
        c=self.client([(401,{},{}),(200,{'success':True,'data':{'type':'at'}},{'galaxy_creator_session_id':'creator-session'}),(200,goods('sku-1'),{}),(200,goods('sku-2'),{})])
        result=c.resolve_goods_bindings(['儿时','非遗'])
        self.assertEqual([r['biz_id'] for r in result],['sku-1','sku-2'])
        calls=c.session.calls
        self.assertEqual(len(calls),4)
        self.assertEqual(calls[1]['url'],'https://customer.xiaohongshu.com/api/cas/customer/web/service-ticket')
        self.assertEqual(calls[1]['body'],{'service':protocol.CREATOR_BASE_URL,'source':'official','type':'tgt'})
        self.assertEqual(calls[1]['cookies']['web_session'],'main-session')
        self.assertIn('x-s',calls[1]['headers'])
        self.assertEqual(calls[0]['body'],calls[2]['body'])
        self.assertEqual(calls[2]['cookies']['galaxy_creator_session_id'],'creator-session')
        self.assertEqual(calls[3]['body']['keyword'],'非遗')
    def test_authorized_query_does_not_exchange_again(self):
        c=self.client([(200,goods(),{})])
        self.assertEqual(len(c.query_goods_list('儿时')),1)
        self.assertEqual(len(c.session.calls),1)
    def test_a_second_401_stops_after_one_exchange(self):
        c=self.client([(401,{},{}),(200,{'success':True},{'galaxy_creator_session_id':'creator-session'}),(401,{}, {})])
        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError,'查询小红书商品失败'):
            c.query_goods_list('儿时')
        self.assertEqual(len(c.session.calls),3)
    def test_failed_exchange_keeps_the_query_failure_fatal(self):
        c=self.client([(401,{},{}),(200,{'success':False,'code':-100},{})])
        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError,'自动获取'):
            c.query_goods_list('儿时')
        self.assertEqual(len(c.session.calls),2)
    def test_non_authentication_errors_do_not_trigger_login(self):
        c=self.client([(403,{}, {})])
        with self.assertRaises(protocol.XiaohongshuProtocolError):
            c.query_goods_list('儿时')
        self.assertEqual(len(c.session.calls),1)
