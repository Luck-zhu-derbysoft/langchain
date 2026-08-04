-- ============================================================================
-- Enterprise Alert Agent - 记忆模块 PostgreSQL 初始化脚本
-- 对应代码: app/infrastructure/memory/redis_postgres_conversation_memory.py
--
-- 用法:
--   1) 新建数据库（PostgreSQL 不支持 CREATE DATABASE IF NOT EXISTS）:
--        CREATE DATABASE enterprise_alert_agent ENCODING 'UTF8';
--      然后更新 .env 中 pg_db=enterprise_alert_agent
--   2) 执行本脚本（在任意库里执行均可，会自动建表）:
--       psql -h 139.224.246.172 -U postgres -d <your_db> -f scripts/init_pg_memory_schema.sql
--       （或 set PGPASSWORD=postgres 后再执行）
--
-- 脚本可重复执行（幂等：CREATE ... IF NOT EXISTS）
-- ============================================================================

-- ============================================================================
-- 表 1: conversation_memory_session  会话级记忆（长期摘要 + 轮次计数）
-- ============================================================================
-- 代码使用说明:
--   - 唯一键 (tenant_id, user_id, thread_id) 用于 ON CONFLICT DO UPDATE 幂等写入
--   - status: 'active' 正常 / 'deleted' 已清除（clear_memory 软删除）
--   - version: 乐观锁/版本号，每次更新 version = version + 1
--   - expires_at: 过期时间，查询会过滤 expires_at <= NOW() 的失效会话
CREATE TABLE IF NOT EXISTS conversation_memory_session (
    tenant_id       VARCHAR(128) NOT NULL,
    user_id         VARCHAR(128) NOT NULL,
    thread_id       VARCHAR(128) NOT NULL,
    memory_summary  TEXT         NOT NULL DEFAULT '',
    turn_count      INTEGER      NOT NULL DEFAULT 0,
    status          VARCHAR(16)  NOT NULL DEFAULT 'active',
    version         INTEGER      NOT NULL DEFAULT 0,
    last_message_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id, thread_id)
);

-- 常用查询: WHERE status='active' AND (expires_at IS NULL OR expires_at > NOW())
CREATE INDEX IF NOT EXISTS idx_session_active_scope
    ON conversation_memory_session (tenant_id, user_id, thread_id)
    WHERE status = 'active';

-- ============================================================================
-- 表 2: conversation_memory_turn  单轮对话记录（近期对话 + 上下文）
-- ============================================================================
-- 代码使用说明:
--   - turn_index: 轮次序号，按 (tenant_id,user_id,thread_id) 内递增
--   - role: 'user' / 'assistant' / 'system'
--   - metadata: 附加信息，代码以 JSON 字符串写入（json.dumps）
--   - is_deleted: 软删除标记，clear_memory 置为 TRUE
CREATE TABLE IF NOT EXISTS conversation_memory_turn (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   VARCHAR(128) NOT NULL,
    user_id     VARCHAR(128) NOT NULL,
    thread_id   VARCHAR(128) NOT NULL,
    turn_index  INTEGER      NOT NULL,
    role        VARCHAR(32)  NOT NULL,
    content     TEXT         NOT NULL,
    metadata    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    is_deleted  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, user_id, thread_id, turn_index)
);

-- 常用查询: WHERE tenant_id=? AND user_id=? AND thread_id=? AND is_deleted=FALSE
--           ORDER BY turn_index DESC LIMIT n
CREATE INDEX IF NOT EXISTS idx_turn_scope
    ON conversation_memory_turn (tenant_id, user_id, thread_id, turn_index);

CREATE INDEX IF NOT EXISTS idx_turn_active_scope
    ON conversation_memory_turn (tenant_id, user_id, thread_id)
    WHERE is_deleted = FALSE;

-- ============================================================================
-- 可选的验证语句（建表后自查）
-- ============================================================================
-- \dt conversation_memory_*
-- SELECT * FROM conversation_memory_session LIMIT 1;
-- SELECT * FROM conversation_memory_turn   LIMIT 1;
