-- Kikitori 的資料表，全部加 kikitori_ 前綴，
-- 與同專案既有的 daily 表（Spotify 排行榜資料）互不干擾。

-- 一集節目。以 Spotify 的 episode id 當唯一鍵，
-- 因為 App 手上唯一確定的識別就是它。
create table if not exists public.kikitori_episodes (
    id                  uuid primary key default gen_random_uuid(),
    spotify_episode_id  text unique not null,
    show_name           text not null,
    episode_title       text not null,
    feed_url            text,
    audio_url           text,
    duration_ms         integer,
    language            text,
    created_at          timestamptz not null default now()
);

-- 逐字稿。lines 是一個陣列，每個元素長這樣：
--   {"start_ms": 1200, "end_ms": 4300, "text": "原文", "translation": "翻譯"}
-- 用 jsonb 而不是一句一列，是因為 App 每次都整份取用，沒有單句查詢的需求。
create table if not exists public.kikitori_transcripts (
    id              uuid primary key default gen_random_uuid(),
    episode_id      uuid not null references public.kikitori_episodes(id) on delete cascade,
    lines           jsonb not null,
    line_count      integer generated always as (jsonb_array_length(lines)) stored,
    source_model    text,
    language        text,
    translated_to   text,
    -- 該集的詞表與詞邊界，由 backend/vocab.py 在轉錄時預先建好。
    -- 長這樣：{"vocab": {"市場": {"r":"しじょう","zh":[...],"en":[...]}},
    --          "tokens": [[[0,2,"市場"],[2,3,null],...], ...]}
    --
    -- 放在這裡而不是給 App 一本字典，有三個理由：
    -- 日文沒有空格，App 沒辦法自己斷詞（要形態素解析）；
    -- 完整字典 293 MB，綁進 App 太大、進 Supabase 會吃掉免費額度；
    -- 同形異讀要靠語境挑（市場 しじょう vs いちば），那只有轉錄當下才有。
    --
    -- 可為 null —— 舊資料沒有這個欄位，App 要當作「這集還沒有詞表」處理。
    vocab           jsonb,
    created_at      timestamptz not null default now(),
    unique (episode_id)
);

-- 轉錄任務。App 排隊、GitHub Actions 認領並回報進度。
create table if not exists public.kikitori_jobs (
    id                  uuid primary key default gen_random_uuid(),
    spotify_episode_id  text not null,
    show_name           text not null,
    episode_title       text not null,
    duration_ms         integer,
    status              text not null default 'queued',
    stage               text,
    error               text,
    attempts            integer not null default 0,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    constraint kikitori_jobs_status_check
        check (status in ('queued', 'running', 'done', 'failed'))
);

create index if not exists kikitori_jobs_status_idx
    on public.kikitori_jobs (status, created_at);

create index if not exists kikitori_episodes_spotify_idx
    on public.kikitori_episodes (spotify_episode_id);

-- 同一集不要重複排隊
create unique index if not exists kikitori_jobs_pending_uniq
    on public.kikitori_jobs (spotify_episode_id)
    where status in ('queued', 'running');

-- RLS：App 拿 anon key 只能讀，寫入一律由 service role（GitHub Actions）執行。
alter table public.kikitori_episodes    enable row level security;
alter table public.kikitori_transcripts enable row level security;
alter table public.kikitori_jobs        enable row level security;

drop policy if exists kikitori_episodes_read    on public.kikitori_episodes;
drop policy if exists kikitori_transcripts_read on public.kikitori_transcripts;
drop policy if exists kikitori_jobs_read        on public.kikitori_jobs;
drop policy if exists kikitori_jobs_insert      on public.kikitori_jobs;

create policy kikitori_episodes_read
    on public.kikitori_episodes for select to anon, authenticated using (true);

create policy kikitori_transcripts_read
    on public.kikitori_transcripts for select to anon, authenticated using (true);

create policy kikitori_jobs_read
    on public.kikitori_jobs for select to anon, authenticated using (true);

-- App 要能自己排隊，所以開放 anon 新增任務（只能新增，不能改別人的）
create policy kikitori_jobs_insert
    on public.kikitori_jobs for insert to anon, authenticated with check (true);
