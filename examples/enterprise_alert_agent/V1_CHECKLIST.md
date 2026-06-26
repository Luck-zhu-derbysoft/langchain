# V1 版本 - 实现检查清单

## ✅ V1 实现完成标准

### 🎯 3 步检查

#### 第一步：文件创建检查 ✓
- [ ] 目录 `app/infrastructure/agent/` 已创建
- [ ] 文件 `app/infrastructure/agent/a2a_protocol.py` 已创建（50行）
- [ ] 文件内包含 `IntentClassification` 类
- [ ] 文件内包含 `A2AMessage` 类
- [ ] 文件内包含 `MessageType` 枚举
- [ ] Python 代码语法正确，无错误

#### 第二步：文件修改检查 ✓
- [ ] `app/schemas/chat.py` 的 `ChatResponse` 添加了 3 个新字段：
  - [ ] `intent: Optional[str]`
  - [ ] `intent_confidence: Optional[float]`
  - [ ] `trace_id: Optional[str]`

- [ ] `app/application/services/chat_service.py` 的导入添加了：
  - [ ] `from app.infrastructure.agent.a2a_protocol import IntentClassification`
  - [ ] `import json, logging, uuid`

- [ ] `app/application/services/chat_service.py` 的 `ask()` 方法：
  - [ ] 添加了 `trace_id = str(uuid4())`
  - [ ] 调用了 `_classify_intent(req.query)`
  - [ ] 构建了 `IntentClassification` 对象
  - [ ] 返回 `ChatResponse` 时包含新字段

- [ ] `app/application/services/chat_service.py` 添加了 `_classify_intent()` 方法：
  - [ ] 方法接收 `query: str` 参数
  - [ ] 返回 Dict 包含 intent, confidence, category, entities, reasoning
  - [ ] 包含异常处理逻辑
  - [ ] 调用 LLM 进行意图分类

#### 第三步：导入和语法检查 ✓
- [ ] Python 导入无循环依赖
- [ ] 所有引用的类都已定义
- [ ] 没有 NameError 或 ImportError

---

## 🧪 测试检查清单

### 应用启动测试
```bash
python -m uvicorn app.main:app --reload
```
- [ ] 应用能正常启动，无异常
- [ ] 日志显示应用成功启动
- [ ] 端口 8000 能访问

### 单个请求测试
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "查询客户信息"}'
```
- [ ] 能收到 HTTP 200 响应
- [ ] 响应 JSON 格式正确
- [ ] 响应包含 `response` 字段（原有）
- [ ] 响应包含 `intent` 字段（新增）✅
- [ ] 响应包含 `intent_confidence` 字段（新增）✅
- [ ] 响应包含 `trace_id` 字段（新增）✅

### 响应内容检查
```json
{
  "response": "...",
  "intent": "query_customer_info",      // 应该是具体的意图
  "intent_confidence": 0.85,             // 应该在 0-1 之间
  "trace_id": "xxx-xxx-xxx"              // 应该是 UUID 格式
}
```
- [ ] `intent` 不是 "unknown"（表示 LLM 正常工作）
- [ ] `intent_confidence` 在 0-1 之间
- [ ] `trace_id` 是有效的 UUID 格式
- [ ] 多个请求的 `trace_id` 都不相同

### 日志检查
在应用日志中查找：
- [ ] 日志显示 `[trace_id] Processing query: ...`
- [ ] 日志显示 `[trace_id] Intent classification: ...`
- [ ] 日志显示 `[trace_id] Response sent successfully`

---

## 🔧 常见问题排查

### Q1: ImportError: cannot import name 'IntentClassification'
**原因**: a2a_protocol.py 文件位置或内容错误
**解决**:
- [ ] 确认文件在正确位置: `app/infrastructure/agent/a2a_protocol.py`
- [ ] 确认文件包含 `IntentClassification` 类定义
- [ ] 确认 Python 路径包含 `app` 目录

### Q2: ChatResponse 缺少字段错误
**原因**: schemas/chat.py 修改不完整
**解决**:
- [ ] 重新检查 ChatResponse 类
- [ ] 确认添加了 3 个新字段
- [ ] 字段类型与代码一致: `Optional[str]`, `Optional[float]`, `Optional[str]`

### Q3: intent 总是返回 "unknown"
**原因**: LLM 调用失败或 JSON 解析失败
**解决**:
- [ ] 检查日志中的错误信息
- [ ] 确认 `self.model_client` 正常工作
- [ ] 检查 LLM 响应格式是否为有效 JSON
- [ ] 可以在 `_classify_intent()` 中添加 `print()` 调试

### Q4: 应用启动时报 AttributeError
**原因**: 修改的方法签名或逻辑错误
**解决**:
- [ ] 检查 `ask()` 方法的 async 定义
- [ ] 确认 `_classify_intent()` 是 async 方法
- [ ] 检查 await 关键字是否正确使用

### Q5: 请求超时
**原因**: LLM 调用太慢或卡住
**解决**:
- [ ] 添加请求超时限制
- [ ] 检查 LLM 服务是否正常
- [ ] 试试更简单的提示词

---

## 📊 性能预期

| 指标 | V1 期望值 |
|-----|---------|
| 应用启动时间 | < 5 秒 |
| 单个请求响应时间 | 1-3 秒（主要是 LLM 调用） |
| 内存占用 | 相比之前增加 < 10MB |
| CPU 占用 | 单个请求 < 5% |

---

## 📋 完整检查列表

```
V1 实现检查清单
├── 文件创建检查
│   ├── a2a_protocol.py 创建 ............ [ ]
│   ├── IntentClassification 定义 ....... [ ]
│   └── A2AMessage 定义 ................ [ ]
├── 文件修改检查
│   ├── ChatResponse 字段添加 ........... [ ]
│   ├── ChatService 导入 ............... [ ]
│   ├── ask() 方法修改 ................. [ ]
│   └── _classify_intent() 方法 ........ [ ]
├── 应用启动检查
│   ├── 无异常启动 ..................... [ ]
│   ├── 端口 8000 响应 ................ [ ]
│   └── 日志正常输出 .................. [ ]
├── 功能测试检查
│   ├── 单个请求返回 200 .............. [ ]
│   ├── 响应包含意图字段 .............. [ ]
│   ├── 置信度在 0-1 之间 ............. [ ]
│   └── trace_id 格式正确 ............. [ ]
└── 完成标记
    └── V1 版本完成 ✅ ............... [ ]
```

---

## ✨ V1 实现成功标志

当以下所有条件都满足时，V1 版本实现完成：

1. ✅ 应用能启动，无导入错误
2. ✅ 单个请求能返回 HTTP 200
3. ✅ 响应包含 `intent` 字段（非 "unknown"）
4. ✅ 响应包含 `intent_confidence` 字段（0-1）
5. ✅ 响应包含 `trace_id` 字段（UUID 格式）
6. ✅ 日志显示意图分类过程
7. ✅ 不同请求有不同的 trace_id

---

## 🎉 下一步

V1 完成后，你可以：
- ✅ 看到用户查询的意图分类结果
- ✅ 根据需要优化意图分类提示词
- ✅ 添加更多意图类型

**准备好进阶到 V2 吗？**

V2 将添加：
- 工具选择理由
- 工具备选列表
- 置信度分析

查看: **V2_MODIFICATIONS.md** (即将推出)
