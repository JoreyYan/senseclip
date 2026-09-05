-- User credits balance (lazy-initialized with 3 free credits on first use)
CREATE TABLE IF NOT EXISTS user_credits (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  balance INTEGER NOT NULL DEFAULT 50 CHECK (balance >= 0),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE user_credits ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own credits"
  ON user_credits FOR SELECT
  USING (auth.uid() = user_id);

CREATE OR REPLACE FUNCTION update_user_credits_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER user_credits_updated_at
  BEFORE UPDATE ON user_credits
  FOR EACH ROW EXECUTE FUNCTION update_user_credits_updated_at();

-- Credit transaction audit log
CREATE TABLE IF NOT EXISTS credit_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  amount INTEGER NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('signup_bonus', 'purchase', 'admin_adjustment', 'ingest', 'chat')),
  description TEXT,
  stripe_session_id TEXT,
  job_id TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own transactions"
  ON credit_transactions FOR SELECT
  USING (auth.uid() = user_id);

CREATE INDEX idx_credit_transactions_user_id ON credit_transactions(user_id);
CREATE INDEX idx_credit_transactions_stripe_session
  ON credit_transactions(stripe_session_id)
  WHERE stripe_session_id IS NOT NULL;
