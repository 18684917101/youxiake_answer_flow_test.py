#!/usr/bin/env python3
"""
Youxiake answer-flow regression script.

Usage:
  # Inspect request flow without mutating remote state.
  python3 youxiake_answer_flow_test.py --question-id 5 --dry-run

  # Run with explicit options by index, 0-based: first option, first option, second option.
  python3 youxiake_answer_flow_test.py --question-id 5 --option-indexes 0,0,1

  # Run with explicit option IDs.
  python3 youxiake_answer_flow_test.py --question-id 5 --option-ids 4765,4768,4772
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None  # type: ignore[assignment]


BASE_URL = "https://h5.youxiake.com"
START_PATH = "/api/topic/web/h5/activity/question/start/answer"
SAVE_PATH = "/api/topic/web/h5/activity/question/save/answer"
END_PATH = "/api/topic/web/h5/activity/question/end/answer"
DEFAULT_AUTH = (
    "Bearer "
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxNDAyODk3NyIsImlhdCI6MTc3OTUzMjQ3NywiZXhwIjoxNzgyMTI0NDc3LCJuYmYiOjE3Nzk1MzI0Nzd9."
    "Hlt3G6YOWbhXDeE8Ao3mYdKYSrkkml9Hxgv8prJ58sU"
)


@dataclass
class Topic:
    topic_id: int
    title: str
    options: list[dict[str, Any]]


class ApiError(RuntimeError):
    pass


def make_headers(auth: str, referer: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": BASE_URL,
        "Referer": referer,
        "Authorization": auth,
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 youxiake/9.9.4"
        ),
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    }
    return headers


def post_json(
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    dry_run: bool,
    timeout: float,
) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    print(f"\nPOST {url}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if dry_run:
        return {"code": 200, "msg": "DryRun", "data": {}}

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(url, data=body, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise ApiError(f"request failed: {exc}") from exc

    print(text)
    data = json.loads(text)
    if data.get("code") != 200:
        raise ApiError(f"API code is not 200: {data}")
    return data


def parse_topic(raw: dict[str, Any]) -> Topic:
    return Topic(
        topic_id=int(raw["topicId"]),
        title=str(raw["topicTitle"]),
        options=list(raw.get("optionList") or []),
    )


def ask_claude_for_option(topic: Topic) -> int:
    if _anthropic is None:
        raise RuntimeError("anthropic 包未安装，请运行: pip install anthropic")

    client = _anthropic.Anthropic()
    options_text = "\n".join(
        f"  [{i}] {opt['optionTitle']}"
        for i, opt in enumerate(topic.options)
    )
    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": (
                f"请根据以下题目选择最合适的答案，只回复一个数字（选项索引，从0开始）。\n\n"
                f"题目: {topic.title}\n\n选项:\n{options_text}\n\n只回复数字："
            ),
        }],
    )
    raw = message.content[0].text.strip()
    print(f"  Claude 选择: {raw}")
    for ch in raw:
        if ch.isdigit():
            idx = int(ch)
            if 0 <= idx < len(topic.options):
                return int(topic.options[idx]["optionId"])
    print("  (解析失败，默认选第 0 项)")
    return int(topic.options[0]["optionId"])


def choose_option(
    topic: Topic,
    step: int,
    option_ids: list[int],
    option_indexes: list[int],
    *,
    ask_claude: bool = False,
) -> int:
    if step < len(option_ids):
        return option_ids[step]

    if ask_claude:
        return ask_claude_for_option(topic)

    index = option_indexes[step] if step < len(option_indexes) else 0
    if index < 0 or index >= len(topic.options):
        raise ValueError(
            f"topic {topic.topic_id} has {len(topic.options)} options, invalid index: {index}"
        )
    return int(topic.options[index]["optionId"])


def print_topic(topic: Topic) -> None:
    print(f"\n题目 {topic.topic_id}: {topic.title}")
    for index, option in enumerate(topic.options):
        print(f"  [{index}] {option['optionId']} {option['optionTitle']}")


def run_flow(args: argparse.Namespace) -> dict[str, Any]:
    auth = os.environ.get("YOUXIAKE_AUTH") or DEFAULT_AUTH

    start_headers = make_headers(
        auth,
        f"{BASE_URL}/answers/{args.question_id}?spm=201.101.1.14",
    )
    detail_headers = make_headers(
        auth,
        f"{BASE_URL}/answers/{args.question_id}/detail",
    )

    option_ids = [int(item) for item in args.option_ids.split(",") if item.strip()]
    option_indexes = [int(item) for item in args.option_indexes.split(",") if item.strip()]

    start_data = post_json(
        START_PATH,
        {"questionId": args.question_id},
        start_headers,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )

    if args.dry_run:
        print("\ndry-run 已结束；真实运行会根据 start 接口返回的 topic 继续提交答案并结算。")
        return start_data

    user_answer_record_id = int(start_data["data"]["userAnswerRecordId"])
    topic = parse_topic(start_data["data"]["topic"])
    step = 0

    while True:
        print_topic(topic)
        option_id = choose_option(topic, step, option_ids, option_indexes, ask_claude=args.ask_claude)

        save_data = post_json(
            SAVE_PATH,
            {
                "userAnswerRecordId": user_answer_record_id,
                "optionId": option_id,
                "questionId": args.question_id,
                "topicId": topic.topic_id,
            },
            detail_headers,
            dry_run=False,
            timeout=args.timeout,
        )

        result = save_data["data"]
        print(
            "提交结果:",
            {
                "correctFlag": result.get("correctFlag"),
                "correctOptionId": result.get("correctOptionId"),
                "rebornFlag": result.get("rebornFlag"),
            },
        )

        next_topic = result.get("nextTopic")
        if not next_topic:
            break

        topic = parse_topic(next_topic)
        step += 1
        time.sleep(args.interval)

    end_data = post_json(
        END_PATH,
        {
            "userAnswerRecordId": user_answer_record_id,
            "questionId": args.question_id,
        },
        detail_headers,
        dry_run=False,
        timeout=args.timeout,
    )
    print("\n结算:")
    print(json.dumps(end_data.get("data"), ensure_ascii=False, indent=2))
    return end_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question-id", default="5")
    parser.add_argument("--option-ids", default="")
    parser.add_argument("--option-indexes", default="0")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ask-claude", action="store_true", help="让 Claude AI 自动选答案")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        run_flow(args)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
