#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量检测 Gemini API 密钥是否可用 / 是否支持指定模型。
输入：txt 文件（每行一个密钥）
输出：results.csv + success.txt（有效且支持该模型的密钥）
"""

import asyncio
import aiohttp
import argparse
import csv
import time
from typing import List

DEFAULT_MODEL = "gemini-2.5-computer-use-preview-10-2025"  # 默认模型，可命令行覆盖
DEFAULT_ENDPOINT_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_CONCURRENCY = 20
DEFAULT_TIMEOUT = 10
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
BACKOFF_FACTOR = 2.0

TEST_PAYLOAD = {
    "contents": [{"parts": [{"text": "hi"}]}]
}


async def test_key(session: aiohttp.ClientSession, key: str, endpoint: str, timeout: int) -> dict:
    headers = {
        "x-goog-api-key": key,
        "Content-Type": "application/json"
    }

    backoff = INITIAL_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.post(endpoint, json=TEST_PAYLOAD, headers=headers, timeout=timeout) as resp:
                text = await resp.text()
                code = resp.status

                if 200 <= code < 300:
                    return {"key": key, "status": "valid", "http_status": code, "detail": "OK"}
                if code in (401, 403):
                    return {"key": key, "status": "invalid", "http_status": code, "detail": text[:500]}
                if code == 404:
                    return {"key": key, "status": "model_not_found", "http_status": code, "detail": text[:500]}
                if code == 429 and attempt < MAX_RETRIES:
                    await asyncio.sleep(backoff)
                    backoff *= BACKOFF_FACTOR
                    continue
                return {"key": key, "status": "error", "http_status": code, "detail": text[:500]}
        except asyncio.TimeoutError:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= BACKOFF_FACTOR
                continue
            return {"key": key, "status": "error", "http_status": -1, "detail": "Timeout"}
        except aiohttp.ClientError as e:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= BACKOFF_FACTOR
                continue
            return {"key": key, "status": "error", "http_status": -1, "detail": f"ClientError: {str(e)[:300]}"}
        except Exception as e:
            return {"key": key, "status": "error", "http_status": -2, "detail": f"Unexpected: {str(e)[:300]}"}


async def worker(semaphore: asyncio.Semaphore, session: aiohttp.ClientSession,
                 key: str, endpoint: str, timeout: int,
                 results: List[dict], idx: int, total: int, success_set: set):
    async with semaphore:
        start = time.time()
        r = await test_key(session, key, endpoint, timeout)
        r["_elapsed"] = round(time.time() - start, 2)
        results.append(r)

        if r["status"] == "valid":
            success_set.add(key)

        print(f"[{idx}/{total}] key={key[:6]}... status={r['status']} http={r['http_status']} t={r['_elapsed']}s")


async def main_async(input_file, output_file, success_file, concurrency, timeout, model):
    with open(input_file, "r", encoding="utf-8") as f:
        keys = [line.strip() for line in f if line.strip()]
    total = len(keys)
    if total == 0:
        print("输入文件没有密钥。")
        return

    endpoint = DEFAULT_ENDPOINT_TEMPLATE.format(model=model)
    semaphore = asyncio.Semaphore(concurrency)
    timeout_cfg = aiohttp.ClientTimeout(total=None, sock_connect=timeout, sock_read=timeout)
    results = []
    success_set = set()

    connector = aiohttp.TCPConnector(limit_per_host=concurrency, ssl=True)
    async with aiohttp.ClientSession(timeout=timeout_cfg, connector=connector) as session:
        tasks = []
        for idx, key in enumerate(keys, start=1):
            t = asyncio.create_task(worker(semaphore, session, key, endpoint, timeout, results, idx, total, success_set))
            tasks.append(t)
        await asyncio.gather(*tasks)

    # 写 results.csv
    with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["key", "status", "http_status", "detail", "elapsed_seconds"])
        for r in results:
            writer.writerow([r["key"], r["status"], r["http_status"], r["detail"], r.get("_elapsed", "")])

    # 写 success.txt（只写有效 key）
    if success_set:
        with open(success_file, "w", encoding="utf-8") as f:
            for k in sorted(success_set):
                f.write(k + "\n")

    valid = len(success_set)
    invalid = sum(1 for r in results if r["status"] == "invalid")
    model_not_found = sum(1 for r in results if r["status"] == "model_not_found")
    error = total - valid - invalid - model_not_found
    print(f"\n完成：总 {total}，✅ 有效 {valid}，🚫 无效 {invalid}，📦 模型不存在 {model_not_found}，⚠️ 其他错误 {error}。")
    if success_set:
        print(f"成功密钥已写入 {success_file}")


def parse_args():
    p = argparse.ArgumentParser(description="批量检测 Gemini API keys 是否支持指定模型")
    p.add_argument("input", help="txt 文件，每行一个密钥")
    p.add_argument("--output", "-o", default="results.csv", help="输出 CSV 文件（默认 results.csv）")
    p.add_argument("--success", "-s", default="success.txt", help="成功密钥输出文件（默认 success.txt）")
    p.add_argument("--concurrency", "-c", type=int, default=DEFAULT_CONCURRENCY, help="并发数（默认 20）")
    p.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT, help="请求超时（秒）（默认 10）")
    p.add_argument("--model", "-m", default=DEFAULT_MODEL, help=f"要测试的模型名（默认 {DEFAULT_MODEL}）")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(main_async(args.input, args.output, args.success, args.concurrency, args.timeout, args.model))
    except KeyboardInterrupt:
        print("被用户中断。")
