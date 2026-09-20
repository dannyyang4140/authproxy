"""命令行管理，便于在容器内签发 / 管理令牌：

    docker exec -it <容器> python -m app.cli issue --name 法务小张 \
        --datasets ds_001,ds_002 --scope retrieve --expire-days 90
    docker exec -it <容器> python -m app.cli list
    docker exec -it <容器> python -m app.cli disable <token_id>
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from .admin import _normalize_datasets
from .config import get_settings
from .db import SessionLocal, init_db
from .models import AccessToken
from .schemas import LEVEL_SCOPE, SCOPE_LEVEL
from .security import generate_access_token


def _fmt_dt(value) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M")


def issue(args) -> None:
    db = SessionLocal()
    try:
        plain, prefix, token_hash = generate_access_token()
        datasets = _normalize_datasets([d for d in args.datasets.split(",") if d.strip()]) if args.datasets else []
        expires = datetime.now(timezone.utc) + timedelta(days=args.expire_days) if args.expire_days else None
        token = AccessToken(
            name=args.name.strip(),
            token_hash=token_hash,
            token_prefix=prefix,
            scope_level=SCOPE_LEVEL[args.scope],
            enabled=True,
            expires_at=expires,
            rate_limit_per_min=args.rate_limit,
            note=args.note or "",
        )
        token.datasets = datasets
        db.add(token)
        db.commit()
        db.refresh(token)
        print("✅ 已签发访问令牌（明文仅显示一次，请妥善保存）：\n")
        print(plain + "\n")
        print(f"ID:        {token.id}")
        print(f"名称:      {token.name}")
        print(f"权限:      {args.scope}")
        print(f"数据集:    {', '.join(datasets) if datasets else '(空，仅可访问无数据集资源)'}")
        print(f"有效期至:  {_fmt_dt(expires)}")
        print(f"限流/分钟: {args.rate_limit or '不限'}")
    finally:
        db.close()


def list_tokens(_args) -> None:
    db = SessionLocal()
    try:
        rows = db.query(AccessToken).order_by(AccessToken.created_at.desc()).all()
        if not rows:
            print("（暂无令牌）")
            return
        header = f"{'ID(前8位)':<10}{'名称':<16}{'权限':<10}{'启用':<6}{'有效期至':<18}数据集"
        print(header)
        print("-" * len(header.encode("gbk", errors="ignore")))
        for t in rows:
            ds = ", ".join(t.datasets) if t.datasets else "-"
            print(
                f"{t.id[:8]:<10}{t.name[:14]:<16}{LEVEL_SCOPE.get(t.scope_level, '?'):<10}"
                f"{('是' if t.enabled else '否'):<6}{_fmt_dt(t.expires_at):<18}{ds}"
            )
    finally:
        db.close()


def _set_enabled(token_id: str, enabled: bool) -> None:
    db = SessionLocal()
    try:
        token = db.get(AccessToken, token_id)
        if token is None:
            print(f"未找到令牌：{token_id}")
            return
        token.enabled = enabled
        db.commit()
        print(f"已{'启用' if enabled else '停用'}：{token.name}（{token.id[:8]}）")
    finally:
        db.close()


def delete(args) -> None:
    db = SessionLocal()
    try:
        token = db.get(AccessToken, args.token_id)
        if token is None:
            print(f"未找到令牌：{args.token_id}")
            return
        db.delete(token)
        db.commit()
        print(f"已删除：{token.name}（{args.token_id}）")
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAGFlow 鉴权代理令牌管理")
    sub = parser.add_subparsers(dest="command", required=True)

    p_issue = sub.add_parser("issue", help="签发令牌")
    p_issue.add_argument("--name", required=True, help="成员或 Agent 名称")
    p_issue.add_argument("--datasets", default="", help="数据集 ID，逗号分隔；* 表示全部")
    p_issue.add_argument("--scope", choices=list(SCOPE_LEVEL.keys()), default="retrieve")
    p_issue.add_argument("--expire-days", type=int, default=0, help="有效天数；0 表示永不过期")
    p_issue.add_argument("--rate-limit", type=int, default=0, help="每分钟限流；0 不限")
    p_issue.add_argument("--note", default="")
    p_issue.set_defaults(func=issue)

    p_list = sub.add_parser("list", help="列出令牌")
    p_list.set_defaults(func=list_tokens)

    p_disable = sub.add_parser("disable", help="停用令牌")
    p_disable.add_argument("token_id")
    p_disable.set_defaults(func=lambda a: _set_enabled(a.token_id, False))

    p_enable = sub.add_parser("enable", help="启用令牌")
    p_enable.add_argument("token_id")
    p_enable.set_defaults(func=lambda a: _set_enabled(a.token_id, True))

    p_del = sub.add_parser("delete", help="删除令牌")
    p_del.add_argument("token_id")
    p_del.set_defaults(func=delete)
    return parser


def main() -> None:
    get_settings()
    init_db()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
