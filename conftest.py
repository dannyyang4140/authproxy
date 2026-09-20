"""pytest 全局环境：在导入应用前把配置指向临时数据库。"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="rap_test_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "test.db")
os.environ.setdefault("RAGFLOW_BASE_URL", "http://ragflow.test")
os.environ.setdefault("RAGFLOW_API_KEY", "upstream-secret")
os.environ.setdefault("ADMIN_TOKEN", "admin-secret")
os.environ.setdefault("ADMIN_TOKEN_FILE", os.path.join(_tmp, "admin_token.txt"))
os.environ.setdefault("STRICT_RESOURCE_BINDING", "true")
