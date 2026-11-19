"""A tiny but representative slice of Kite's instrument CSV dump."""

from __future__ import annotations

HEADER = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,"
    "lot_size,instrument_type,segment,exchange"
)

ROWS = [
    # NSE cash equities
    "738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,,0.1,1,EQ,NSE,NSE",
    "408065,1594,INFY,INFOSYS,0,,,0.05,1,EQ,NSE,NSE",
    "2953217,11536,TCS,TATA CONSULTANCY SERV LT,0,,,0.1,1,EQ,NSE,NSE",
    "3329,13,ABB,ABB INDIA,0,,,0.5,1,EQ,NSE,NSE",  # no F&O -> not in universe
    # NSE indices
    "256265,1001,NIFTY 50,,0,,,0.0,0,EQ,INDICES,NSE",
    "260105,1016,NIFTY BANK,,0,,,0.0,0,EQ,INDICES,NSE",
    "257801,1007,NIFTY FIN SERVICE,,0,,,0.0,0,EQ,INDICES,NSE",
    "288009,1125,NIFTY MID SELECT,,0,,,0.0,0,EQ,INDICES,NSE",
    # BSE indices (kept) and a BSE equity (dropped)
    "265,1,SENSEX,,0,,,0.0,0,EQ,INDICES,BSE",
    "274441,1072,BANKEX,,0,,,0.0,0,EQ,INDICES,BSE",
    "128083204,500325,RELIANCE,RELIANCE INDUSTRIES,0,,,0.05,1,EQ,BSE,BSE",
    # NFO stock futures + an index future + an option
    "13238786,51714,RELIANCE26NOVFUT,RELIANCE,0,2026-11-26,,0.1,500,FUT,NFO-FUT,NFO",
    "13240322,51720,INFY26NOVFUT,INFY,0,2026-11-26,,0.1,400,FUT,NFO-FUT,NFO",
    "13241090,51723,TCS26NOVFUT,TCS,0,2026-11-26,,0.1,175,FUT,NFO-FUT,NFO",
    "13368834,52222,NIFTY26NOVFUT,NIFTY,0,2026-11-26,,0.1,75,FUT,NFO-FUT,NFO",
    "13371138,52231,NIFTY26NOV24500CE,NIFTY,0,2026-11-26,24500,0.05,75,CE,NFO-OPT,NFO",
    # MCX row (dropped)
    "109134343,426306,CRUDEOIL26DECFUT,CRUDEOIL,0,2026-12-18,,1.0,100,FUT,MCX-FUT,MCX",
]


def instruments_csv(*extra_rows: str, header: str = HEADER) -> str:
    return "\n".join([header, *ROWS, *extra_rows]) + "\n"
