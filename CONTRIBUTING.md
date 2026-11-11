# 贡献指南

感谢关注「红笔」！本项目采用 PolyForm Noncommercial 1.0.0 协议（见 LICENSE）：**个人与教育场景自由使用，禁止商业用途**。

## 如何参与

1. **提 Issue**：发现 bug 或想提功能建议，请开 Issue，尽量附上：使用的系统（Windows 版本）、浏览器、复现步骤、截图。
2. **提 PR**：
   - Fork → 新建分支 → 修改 → 提交 PR 到 main
   - 一次 PR 只做一件事（一个功能或一个修复）
   - 新功能必须带冒烟测试（参考 `tests/smoke_*.py` 现有写法）
   - 保证本地 `python -c "import backend.app"` 与 CI 通过
3. **代码约定**：
   - 后端 FastAPI + SQLAlchemy，路由放 `backend/routes/`，业务逻辑放 `backend/models/`
   - 不提交 API Key、密码等敏感信息；`data/`、`*.db` 已在 .gitignore，勿强行添加
   - 提交信息用中文，格式：`模块: 做了什么`

## 开发环境

```bash
pip install -r requirements.txt
python -m uvicorn backend.app:app --port 8080
# 访问 http://localhost:8080，默认 PIN 123456
```
