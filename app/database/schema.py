# app/database/schema.py

DB_SCHEMA = """
-- Enable foreign key support globally
PRAGMA foreign_keys = ON;

-- Basic configuration table for application settings
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at INTEGER
);

-- User information table - basic platform-wide attributes
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    first_seen INTEGER NOT NULL,
    account_created_utc INTEGER,
    is_suspended BOOLEAN,
    karma_post INTEGER,
    karma_comment INTEGER,
    verified BOOLEAN,
    last_updated INTEGER,
    update_frequency INTEGER DEFAULT 86400
);

-- User roles and flairs per subreddit
CREATE TABLE IF NOT EXISTS user_subreddit_roles (
    username TEXT,
    subreddit TEXT,
    is_mod BOOLEAN,
    is_approved BOOLEAN,
    flair_text TEXT,
    flair_css TEXT,
    last_updated INTEGER,
    PRIMARY KEY (username, subreddit),
    FOREIGN KEY(username) REFERENCES users(username),
    FOREIGN KEY(subreddit) REFERENCES subreddits(name)
);

-- Subreddit information table - contains metadata and monitoring status
CREATE TABLE IF NOT EXISTS subreddits (
    name TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    subscriber_count INTEGER,
    over_18 BOOLEAN,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    last_updated INTEGER,
    refresh_frequency INTEGER DEFAULT 604800,  -- 7 days in seconds
    last_metadata_refresh INTEGER,
    last_successful_fetch TEXT,  -- 'hot' or 'top'
    posts_indexed INTEGER DEFAULT 0,
    last_fetch_cursor TEXT,  -- For resuming pagination
    force_refresh BOOLEAN DEFAULT FALSE,  -- Manual override for refresh schedule
    metadata JSON
);

-- Posts table - core table for Reddit post data
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    subreddit TEXT NOT NULL,
    author TEXT,
    title TEXT,
    url TEXT,
    created_utc INTEGER,
    score INTEGER,
    post_type TEXT NOT NULL,
    selftext TEXT,
    downloaded BOOLEAN DEFAULT 0,
    downloaded_at INTEGER,
    error TEXT,
    media_status TEXT DEFAULT 'pending' CHECK(
        media_status IN (
            'pending', 'downloaded', 'permanently_removed',
            'temporarily_unavailable', 'error', 'duplicate'
        )
    ),
    last_comment_update INTEGER,
    comment_count INTEGER DEFAULT 0,
    expected_comment_count INTEGER DEFAULT NULL,
    comment_fetch_attempts INTEGER DEFAULT 0,
    last_comment_failure TEXT DEFAULT NULL,
    last_status_check INTEGER,
    last_batch_check INTEGER DEFAULT 0,
    metadata JSON,
    FOREIGN KEY(subreddit) REFERENCES subreddits(name)
);

-- Media items associated with posts
CREATE TABLE IF NOT EXISTS post_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    media_url TEXT NOT NULL,
    media_type TEXT NOT NULL,
    original_url TEXT,
    download_path TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    downloaded BOOLEAN DEFAULT 0,
    downloaded_at INTEGER,
    error TEXT,
    download_attempts INTEGER DEFAULT 0,
    last_attempt INTEGER,
    media_status TEXT DEFAULT 'pending' CHECK(
        media_status IN (
            'pending', 'downloaded', 'permanently_removed',
            'temporarily_unavailable', 'error', 'duplicate'
        )
    ),
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

-- Comments table - stores Reddit comments with threading info
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    parent_id TEXT,
    author TEXT,
    body TEXT,
    created_utc INTEGER,
    score INTEGER,
    edited INTEGER,
    depth INTEGER DEFAULT 0,
    path TEXT NOT NULL,
    downloaded_at INTEGER,
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

-- Media deduplication tracking
CREATE TABLE IF NOT EXISTS media_deduplication (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_hash TEXT NOT NULL,
    quick_hash TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    first_seen_timestamp INTEGER NOT NULL,
    first_seen_post_id TEXT,
    total_size INTEGER NOT NULL,
    mime_type TEXT,
    duplicate_count INTEGER DEFAULT 0
);

-- Links between duplicate media files
CREATE TABLE IF NOT EXISTS media_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    symlink_path TEXT NOT NULL,
    created_timestamp INTEGER NOT NULL,
    is_crosspost BOOLEAN DEFAULT 0,
    original_post_id TEXT,
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

-- Worker status tracking
CREATE TABLE IF NOT EXISTS worker_status (
    worker_type TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    last_updated INTEGER
);

-- Performance optimization indexes
CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);
CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);
CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score);
CREATE INDEX IF NOT EXISTS idx_posts_type ON posts(post_type);
CREATE INDEX IF NOT EXISTS idx_posts_downloaded ON posts(downloaded);
CREATE INDEX IF NOT EXISTS idx_posts_media_status ON posts(media_status);
CREATE INDEX IF NOT EXISTS idx_posts_comment_status ON posts(comment_fetch_attempts, expected_comment_count);
CREATE INDEX IF NOT EXISTS idx_posts_batch_check ON posts(last_batch_check);
CREATE INDEX IF NOT EXISTS idx_posts_status_check ON posts(last_status_check);
CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id);
CREATE INDEX IF NOT EXISTS idx_post_media_downloaded ON post_media(downloaded);
CREATE INDEX IF NOT EXISTS idx_post_media_status ON post_media(media_status);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_comments_path ON comments(path);
CREATE INDEX IF NOT EXISTS idx_comments_author ON comments(author);
CREATE INDEX IF NOT EXISTS idx_users_last_updated ON users(last_updated);
CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_subreddit_roles(username);
CREATE INDEX IF NOT EXISTS idx_user_roles_sub ON user_subreddit_roles(subreddit);
CREATE INDEX IF NOT EXISTS idx_media_quick_hash ON media_deduplication(quick_hash);
CREATE INDEX IF NOT EXISTS idx_media_canonical_hash ON media_deduplication(canonical_hash);
CREATE INDEX IF NOT EXISTS idx_media_links_hash ON media_links(canonical_hash);
CREATE INDEX IF NOT EXISTS idx_subreddits_refresh ON subreddits(last_metadata_refresh);
CREATE INDEX IF NOT EXISTS idx_posts_subreddit_downloaded ON posts(subreddit, downloaded);
CREATE INDEX IF NOT EXISTS idx_media_status ON post_media(media_status, downloaded);

-- Initial configuration values
INSERT OR REPLACE INTO config (key, value, description, updated_at) VALUES
    ('nsfw_mode', 'false', 'Whether NSFW content is enabled', CAST(strftime('%s', 'now') AS INTEGER)),
    ('batch_size', '50', 'Number of posts to process in each batch', CAST(strftime('%s', 'now') AS INTEGER)),
    ('auto_discover_enabled', 'true', 'Whether to automatically discover and archive new subreddits', CAST(strftime('%s', 'now') AS INTEGER)),
    ('min_subscribers', '10000', 'Minimum subscriber count for auto-discovery', CAST(strftime('%s', 'now') AS INTEGER)),
    ('download_comments', 'true', 'Whether to download comments for posts', CAST(strftime('%s', 'now') AS INTEGER)),
    ('comment_depth', '10', 'Maximum depth of comments to download', CAST(strftime('%s', 'now') AS INTEGER)),
    ('subreddit_duplicate_strategy', 'highest_voted', 'Strategy for handling duplicate media within a subreddit (highest_voted or oldest)', CAST(strftime('%s', 'now') AS INTEGER));
    
-- Initialize worker status
INSERT OR IGNORE INTO worker_status (worker_type, enabled, last_updated) VALUES
    ('media', 0, CAST(strftime('%s', 'now') AS INTEGER)),
    ('comments', 0, CAST(strftime('%s', 'now') AS INTEGER)),
    ('metadata', 0, CAST(strftime('%s', 'now') AS INTEGER));
"""