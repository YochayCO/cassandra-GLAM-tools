ALTER TABLE images ADD COLUMN IF NOT EXISTS tot bigint NOT NULL DEFAULT 0;
ALTER TABLE images ADD COLUMN IF NOT EXISTS n_days int NOT NULL DEFAULT 0;
ALTER TABLE images ADD COLUMN IF NOT EXISTS avg double precision;
ALTER TABLE images ADD COLUMN IF NOT EXISTS median double precision;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS last_crawled_at timestamptz;
ALTER TABLE usages ADD COLUMN IF NOT EXISTS gil_page int;

CREATE TABLE IF NOT EXISTS snapshot_state (
  views_loaded_for date,
  tree_crawled_at timestamptz
);

CREATE TABLE IF NOT EXISTS visualizations_sum_t (
  access_date date PRIMARY KEY,
  accesses_sum bigint NOT NULL
);

DROP MATERIALIZED VIEW IF EXISTS visualizations_stats;
DROP MATERIALIZED VIEW IF EXISTS visualizations_sum;

CREATE OR REPLACE VIEW visualizations_stats AS
  SELECT img_name, tot, avg, median
  FROM images
  WHERE COALESCE(is_alive, false) = true AND n_days > 0;

CREATE OR REPLACE VIEW visualizations_sum AS
  SELECT accesses_sum, access_date FROM visualizations_sum_t;

INSERT INTO visualizations_sum_t (access_date, accesses_sum)
SELECT access_date, SUM(accesses)
FROM visualizations
GROUP BY access_date
ON CONFLICT (access_date) DO UPDATE SET accesses_sum = EXCLUDED.accesses_sum;

UPDATE images i SET
  tot = s.tot,
  n_days = s.n_days,
  avg = s.avg,
  median = s.median
FROM (
  SELECT media_id,
         SUM(accesses) AS tot,
         COUNT(*) AS n_days,
         AVG(accesses) AS avg,
         PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY accesses) AS median
  FROM visualizations
  GROUP BY media_id
) s
WHERE i.media_id = s.media_id;
