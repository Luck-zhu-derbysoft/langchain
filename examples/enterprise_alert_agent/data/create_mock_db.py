import os
import sqlite3

# Ensure data directory exists
os.makedirs("data", exist_ok=True)

db_path = "data/app.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. 创建一个模拟的告警历史表 (对应你工具描述里的数据)
cursor.execute('''
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_name TEXT,
    status TEXT,
    device_ip TEXT,
    message TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
''')

# 2. 插入几条好玩的模拟告警数据
cursor.executemany('''
INSERT INTO alert_history (alert_name, status, device_ip, message) VALUES (?, ?, ?, ?)
''', [
    ("CPU_Usage_High", "active", "192.168.1.100", "CPU使用率超过95%"),
    ("Memory_Leak", "resolved", "192.168.1.101", "内存占用持续上升"),
    ("Disk_Full", "active", "192.168.1.102", "根分区磁盘空间不足")
])

# 3. 创建一个模拟的资产配置表 (CMDB)
cursor.execute('''
CREATE TABLE IF NOT EXISTS cmdb_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT,
    ip TEXT,
    role TEXT,
    owner TEXT
)
''')

cursor.executemany('''
INSERT INTO cmdb_assets (hostname, ip, role, owner) VALUES (?, ?, ?, ?)
''', [
    ("web-server-01", "192.168.1.100", "Frontend", "Alex"),
    ("db-server-01", "192.168.1.101", "Database", "Bob"),
    ("auth-service", "192.168.1.102", "Auth", "Charlie")
])

conn.commit()
conn.close()
print(f"成功创建模拟数据库并初始化测试数据：{db_path}")
