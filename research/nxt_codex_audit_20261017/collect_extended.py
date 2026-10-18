# Codex 수정: CODX-NXT-001 (2026-10-17), quotes only; no order/account endpoint.
"""Runs only after user approval on the existing production KIS connection.
Fixed request file has date/code pairs derived from PREVIOUS cash-day turnover.
Retrieve NX premarket and KRX full session; this is a data audit, not trading.
"""
import asyncio
import gzip
import json
import sys
from pathlib import Path
from app.backend.kis_quotes import kis_service

PATH='/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice'
TR='FHKST03010230'
DATES={'20260309','20260323','20260330','20260608','20260803'}
CUTOFFS={'NX':('091000',),'J':('093000','113000','133000','153000')}

async def main():
    root=Path(sys.argv[1]);selection=json.loads((root/'extended_selection.json').read_text())
    assert set(selection)==DATES
    assert all(len(codes)<=30 and all(len(c)==6 and c.isdigit() for c in codes) for codes in selection.values())
    svc=kis_service()
    if not svc.enabled:raise RuntimeError('KIS unavailable')
    token=await svc._load_stored_token()
    auth_mode='stored_token'
    if not token or svc._token[1]-svc._clock()<900:
        # User explicitly approved one quote-access-token renewal after first run failed.
        original_load=svc._load_stored_token
        async def no_short_lived_token():return None
        svc._load_stored_token=no_short_lived_token
        svc._token=None
        token=await svc._access_token()
        svc._load_stored_token=original_load
        auth_mode='renewed_once'
    print(json.dumps({'authentication':auth_mode}),flush=True)
    async def stored_token_only():
        if svc._clock()>=svc._token[1]:raise RuntimeError('Stored token expired')
        return token
    svc._access_token=stored_token_only
    svc.max_rate=svc.rate=2.0
    gate=asyncio.Semaphore(3);result={};count=0
    async def one(date,code,market,hour):
        nonlocal count
        async with gate:
            params={'FID_COND_MRKT_DIV_CODE':market,'FID_INPUT_ISCD':code,
                    'FID_INPUT_HOUR_1':hour,'FID_INPUT_DATE_1':date,
                    'FID_PW_DATA_INCU_YN':'N','FID_FAKE_TICK_INCU_YN':''}
            out={}
            for _ in range(5):
                try:out=await svc._rest_get(PATH,TR,params)
                except Exception as error:out={'rt_cd':'x','msg_cd':type(error).__name__}
                if str(out.get('rt_cd'))=='0':break
                await asyncio.sleep(2)
            bars=[]
            for row in out.get('output2') or []:
                if row.get('stck_bsop_date')!=date:continue
                try:bars.append([int(float(row.get(k) or 0)) for k in
                    ('stck_cntg_hour','stck_oprc','stck_hgpr','stck_lwpr','stck_prpr','cntg_vol')])
                except (ValueError,TypeError):continue
            result.setdefault(date,{}).setdefault(code,{}).setdefault(market,{})[hour]={
                'rt':out.get('rt_cd'),'msg':out.get('msg_cd'),'bars':sorted(bars)}
            count+=1
            if count%100==0:print(json.dumps({'completed_calls':count}),flush=True)
    jobs=[one(d,c,m,h) for d,codes in selection.items() for c in codes for m,hs in CUTOFFS.items() for h in hs]
    await asyncio.gather(*jobs)
    with gzip.open(root/'extended.json.gz','wt') as f:json.dump(result,f,separators=(',',':'))
    ok=sum(str(v['rt'])=='0' for codes in result.values() for markets in codes.values() for hours in markets.values() for v in hours.values())
    print(json.dumps({'calls':count,'ok':ok,'out':'extended.json.gz'}),flush=True)

if __name__=='__main__':asyncio.run(main())
