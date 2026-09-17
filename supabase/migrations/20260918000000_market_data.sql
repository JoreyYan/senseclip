-- 宏观与市场数据采集(engine 的 MarketCollector 写入,api 的 /api/market/* 读取)

-- 序列目录:每个指标一行,记录来源、层级、最新值与采集健康状态
CREATE TABLE IF NOT EXISTS public.market_series (
  id text PRIMARY KEY,                    -- 如 PAYEMS / VIX_CBOE / COT_ES_LEV_NET
  name_zh text NOT NULL,
  layer text NOT NULL,                    -- hard_data / rates / positioning / credit_cross
  priority text NOT NULL DEFAULT 'tier1', -- tier1 必看 / aux 辅助验证
  source text NOT NULL,                   -- fred / cboe / cftc / treasury
  frequency text,                         -- daily / weekly / monthly / quarterly
  unit text,
  transform text,                         -- level / mom_diff / yoy_pct / mom_pct
  last_date date,
  last_value double precision,
  fetched_at timestamptz,
  status text,                            -- ok / error
  error text
);

CREATE TABLE IF NOT EXISTS public.market_observations (
  series_id text NOT NULL,
  obs_date date NOT NULL,
  value double precision NOT NULL,
  PRIMARY KEY (series_id, obs_date)
);

-- 国债拍卖:已公布(未拍)与已完成的拍卖都在这里,结果字段拍卖后才有值
CREATE TABLE IF NOT EXISTS public.treasury_auctions (
  cusip text NOT NULL,
  auction_date date NOT NULL,
  security_type text,
  security_term text,
  original_security_term text,
  reopening boolean,
  announcement_date date,
  issue_date date,
  maturity_date date,
  offering_amt double precision,
  high_yield double precision,
  high_discount_rate double precision,
  bid_to_cover double precision,
  total_accepted double precision,
  primary_dealer_accepted double precision,
  direct_bidder_accepted double precision,
  indirect_bidder_accepted double precision,
  soma_holdings double precision,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cusip, auction_date)
);
CREATE INDEX IF NOT EXISTS treasury_auctions_date_idx ON public.treasury_auctions (auction_date DESC);

-- CFTC 金融期货持仓(Traders in Financial Futures),周频
CREATE TABLE IF NOT EXISTS public.cot_positions (
  contract_code text NOT NULL,
  report_date date NOT NULL,
  market_name text,
  open_interest double precision,
  dealer_net double precision,
  asset_mgr_net double precision,
  lev_money_net double precision,
  other_rept_net double precision,
  nonrept_net double precision,
  raw jsonb,
  PRIMARY KEY (contract_code, report_date)
);

ALTER TABLE public.market_series ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.treasury_auctions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cot_positions ENABLE ROW LEVEL SECURITY;
