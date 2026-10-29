-- Необязательная длительность записи в минутах.
-- NULL — длительность по умолчанию: встреча занимает час, дело без длительности время не занимает.
-- Нужна, чтобы находить пересечения встреч и ставить правильный конец события в календаре.
--
-- На пустом volume применяется автоматически после 001. На уже работающей базе — один раз вручную:
--   docker compose exec -T postgres psql -U assistant -d assistant < db/migrations/002_event_duration.sql

ALTER TABLE events ADD COLUMN IF NOT EXISTS duration_minutes integer;

ALTER TABLE events DROP CONSTRAINT IF EXISTS duration_valid;
ALTER TABLE events ADD CONSTRAINT duration_valid CHECK (
    duration_minutes IS NULL
    OR (duration_minutes BETWEEN 1 AND 1440 AND start_at IS NOT NULL AND type <> 'reminder')
);
