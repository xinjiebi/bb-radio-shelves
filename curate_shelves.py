#!/usr/bin/env python3
"""BB Radio 货架精选池生成 + 健康检查脚本

三种模式:
  无参数              → 生成精选池 (拉取→过滤→清洗→排序→国别均衡→产出 JSON)
  --health-check      → 对已有精选池做流地址可用性检测，输出报告
  --health-check --auto-fix → 检测 + 自动替换失效台

限速纪律：单节点串行，请求间隔 >= 4s，失败指数退避，连续失败 3 次换节点
"""

import json, time, urllib.request, urllib.parse, sys, os, random, re, argparse

# ======================== 配置 ========================
UA = {"User-Agent": "bbradio/0.1 (curation@local)"}
# 健康检查使用 ExoPlayer UA，因为部分服务器（如 shoutcast）对浏览器 UA 返回 HTML 而非音频
UA_EXO = {"User-Agent": "bbradio/0.1 (Linux;ExoPlayer)"}
HOSTS = ["de1.api.radio-browser.info", "nl1.api.radio-browser.info",
         "fr1.api.radio-browser.info", "de2.api.radio-browser.info"]
host_idx = 0
LAST_REQ = 0

POOL_SIZE = 10  # 每货架固定台数（PRD v2.4）
ALLOWED_CODEC = {"MP3", "AAC", "AAC+"}
HEALTH_CHECK_TIMEOUT = 10  # 健康检查超时秒数

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SCRIPT_DIR, "shelves")
REPORT_DIR = os.path.join(SCRIPT_DIR, "health")

SHELVES = [
    ("华语流行",  [{"language": "chinese", "tag": "music"}]),
    ("全球热门",  [{"tag": "music"}]),
    ("老歌怀旧",  [{"tag": t} for t in ["oldies", "retro", "70s", "80s", "90s"]]),
    ("爵士",      [{"tag": t} for t in ["jazz", "blues", "swing"]]),
    ("古典",      [{"tag": t} for t in ["classical", "classic", "instrumental"]]),
    ("电子",      [{"tag": t} for t in ["electronic", "dance", "techno", "house", "trance", "edm"]]),
    ("摇滚",      [{"tag": t} for t in ["rock", "classic rock", "indie", "metal"]]),
    ("轻音乐",    [{"tag": t} for t in ["ambient", "lounge", "easy listening", "chillout"]]),
    ("儿歌",      [{"tag": t} for t in ["children", "childrens music", "children's music"]]),
    ("助眠",      [{"tag": t} for t in ["lullaby", "sleep", "white noise",
                                          "relaxation", "meditating", "meditation", "calm"]]),
]

# ======================== 源归属甄别 ========================
SOURCE_BLACKLIST = [
    "qingting.fm", "qtfm.cn",
    "cdn77.org", "casthost", "profesionalhosting",
    "dribbcast", "myradiostream", "zeno.fm",
    "laut.fm", "radioboss", "live365", "cdnstream",
    "shoutca.st", "sonicpanel", "castradio",
]
RAW_IP_WHITELIST_NAMES = ["radio caprice"]

def is_commercial_source(name, url):
    """返回 True = 商业有主源，应剔除"""
    host = urllib.parse.urlparse(url).netloc.lower()
    if any(k in host for k in SOURCE_BLACKLIST):
        return True
    if "proxy" in url.lower() and not re.search(r"(rthk|bbc|cnr|cri)", host):
        return True
    if re.match(r"^\d+\.\d+\.\d+\.\d+", host):
        return not any(w in name.lower() for w in RAW_IP_WHITELIST_NAMES)
    return False

# ======================== 网络请求 ========================
def get_json(path, ua=None):
    """限速纪律请求：间隔>=4s，失败指数退避，换节点"""
    global LAST_REQ, host_idx
    headers = ua or UA
    for attempt in range(8):
        wait = max(0, 4 - (time.time() - LAST_REQ))
        if wait:
            time.sleep(wait)
        url = f"https://{HOSTS[host_idx]}{path}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                LAST_REQ = time.time()
                return json.loads(r.read().decode())
        except Exception as e:
            LAST_REQ = time.time()
            print(f"    [重试{attempt+1}] {HOSTS[host_idx]}: {e}", file=sys.stderr)
            time.sleep(min(2 ** attempt * 5, 60))
            if attempt >= 2:
                host_idx = (host_idx + 1) % len(HOSTS)
    raise RuntimeError("所有节点均失败")

# ======================== 工具函数 ========================
def clean_name(name):
    return " ".join(name.split())

def norm_url(url):
    """跨货架去重键：去参数、去尾斜杠"""
    return url.split("?")[0].rstrip("/")

def load_existing_pinned(shelf_name):
    """加载已有文件中被人工置顶的条目"""
    fp = os.path.join(OUT_DIR, f"{shelf_name}.json")
    if not os.path.exists(fp):
        return []
    with open(fp, encoding="utf-8") as f:
        old = json.load(f).get("stations", [])
    return [s for s in old if s.get("pinned")]

def load_adult_blacklist():
    """成人黑名单"""
    fp = os.path.join(OUT_DIR, "成人.json")
    if not os.path.exists(fp):
        return set()
    with open(fp, encoding="utf-8") as f:
        return {s["stationuuid"] for s in json.load(f)["stations"]
                if s.get("risk") == "explicit"}

# ======================== 精选池生成 ========================
def curate(name, query_list, existing_pinned=None):
    print(f"\n=== {name} ===")
    merged, seen = {}, set()
    for params in query_list:
        q = {"hidebroken": "true", "limit": "1000"}
        q.update(params)
        path = "/json/stations/search?" + urllib.parse.urlencode(q)
        data = get_json(path)
        tag_desc = ",".join(f"{k}={v}" for k, v in params.items())
        print(f"  [{tag_desc}] 候选 {len(data)} 台")
        for s in data:
            if s["stationuuid"] not in seen:
                seen.add(s["stationuuid"])
                merged[s["stationuuid"]] = s
    data = list(merged.values())
    print(f"  合并去重后: {len(data)} 台")

    # 过滤
    data = [s for s in data
            if s["hls"] == 0 and s["codec"] in ALLOWED_CODEC and s["votes"] > 0]
    print(f"  过滤后: {len(data)} 台")

    # 源归属甄别
    data = [s for s in data
            if not is_commercial_source(s["name"], s["url_resolved"])]
    print(f"  源甄别后: {len(data)} 台")

    # 成人黑名单
    data = [s for s in data if s["stationuuid"] not in ADULT_BLACKLIST]
    print(f"  成人黑名单过滤后: {len(data)} 台")

    # 客户端排序
    data.sort(key=lambda s: -s["votes"])

    # 国别均衡
    max_per_country = max(1, int(POOL_SIZE * 0.4))
    picked, country_cnt = [], {}
    for s in data:
        cc = s["countrycode"] or "??"
        if country_cnt.get(cc, 0) >= max_per_country:
            continue
        picked.append(s)
        country_cnt[cc] = country_cnt.get(cc, 0) + 1
        if len(picked) >= POOL_SIZE:
            break
    print(f"  精选: {len(picked)} 台, 国家数: {len(country_cnt)}")

    return [{
        "stationuuid": s["stationuuid"],
        "name": clean_name(s["name"]),
        "url_resolved": s["url_resolved"],
        "countrycode": s["countrycode"],
        "language": s["language"],
        "tags": s["tags"],
        "codec": s["codec"],
        "bitrate": s["bitrate"],
        "votes": s["votes"],
    } for s in picked]

# ======================== 健康检查 ========================
def check_stream(url):
    """
    检测流地址是否可用。
    返回: ('alive', status_code) | ('dead', reason) | ('timeout', reason)
    """
    global LAST_REQ
    wait = max(0, 4 - (time.time() - LAST_REQ))
    if wait:
        time.sleep(wait)
    try:
        req = urllib.request.Request(url, method="GET", headers=UA_EXO)
        with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as r:
            LAST_REQ = time.time()
            ct = r.headers.get("Content-Type", "")
            status = r.status
            # 接受 audio/* 或 ICY 协议
            if status == 200 and ("audio" in ct.lower() or "icy" in ct.lower() or "octet-stream" in ct.lower()):
                return ("alive", status)
            elif status == 200:
                # 200 但 Content-Type 不像音频，可能是 HTML 跳转页
                return ("dead", f"wrong_content_type: {ct}")
            else:
                return ("dead", f"status={status}")
    except urllib.error.HTTPError as e:
        LAST_REQ = time.time()
        return ("dead", f"http_error_{e.code}")
    except urllib.error.URLError as e:
        LAST_REQ = time.time()
        return ("timeout", str(e.reason))
    except Exception as e:
        LAST_REQ = time.time()
        return ("timeout", str(e))

def find_replacement(shelf_name, exclude_uuids, exclude_urls):
    """为指定货架找一个可用的替换台"""
    shelf_def = None
    for name, params in SHELVES:
        if name == shelf_name:
            shelf_def = params
            break
    if not shelf_def:
        return None

    # 拉取更大范围的候选
    merged, seen = {}, set()
    for params in shelf_def:
        q = {"hidebroken": "true", "limit": "1000"}
        q.update(params)
        path = "/json/stations/search?" + urllib.parse.urlencode(q)
        try:
            data = get_json(path)
        except Exception:
            continue
        for s in data:
            if s["stationuuid"] not in seen:
                seen.add(s["stationuuid"])
                merged[s["stationuuid"]] = s

    candidates = list(merged.values())
    # 过滤
    candidates = [s for s in candidates
                  if s["hls"] == 0 and s["codec"] in ALLOWED_CODEC and s["votes"] > 0]
    candidates = [s for s in candidates
                  if not is_commercial_source(s["name"], s["url_resolved"])]
    candidates = [s for s in candidates if s["stationuuid"] not in ADULT_BLACKLIST]
    candidates = [s for s in candidates if s["stationuuid"] not in exclude_uuids]
    candidates = [s for s in candidates if norm_url(s["url_resolved"]) not in exclude_urls]
    candidates.sort(key=lambda s: -s["votes"])

    # 逐个检测可用性，找到第一个可用的
    for c in candidates[:20]:  # 最多检测 20 个候选
        status, info = check_stream(c["url_resolved"])
        print(f"    候选检测: {c['name'][:40]} → {status}")
        if status == "alive":
            return {
                "stationuuid": c["stationuuid"],
                "name": clean_name(c["name"]),
                "url_resolved": c["url_resolved"],
                "countrycode": c["countrycode"],
                "language": c["language"],
                "tags": c["tags"],
                "codec": c["codec"],
                "bitrate": c["bitrate"],
                "votes": c["votes"],
            }
    return None

def health_check(auto_fix=False):
    """健康检查模式"""
    print("=" * 60)
    print("BB Radio 精选池健康检查")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"模式: {'自动修复' if auto_fix else '仅检测'}")
    print("=" * 60)

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "auto_fix": auto_fix,
        "shelves": []
    }

    total, alive_count, dead_count, timeout_count = 0, 0, 0, 0

    # 加载 index.json 获取货架列表
    index_path = os.path.join(OUT_DIR, "index.json")
    if not os.path.exists(index_path):
        print("错误：找不到 shelves/index.json，请先运行精选池生成。")
        sys.exit(1)

    with open(index_path, encoding="utf-8") as f:
        index_data = json.load(f)

    for shelf_info in index_data["shelves"]:
        shelf_name = shelf_info["shelf"]
        shelf_file = os.path.join(OUT_DIR, f"{shelf_name}.json")
        if not os.path.exists(shelf_file):
            print(f"\n[跳过] {shelf_name}: 文件不存在")
            continue

        with open(shelf_file, encoding="utf-8") as f:
            shelf_data = json.load(f)

        stations = shelf_data.get("stations", [])
        print(f"\n--- {shelf_name} ({len(stations)} 台) ---")

        shelf_report = {"shelf": shelf_name, "stations": [], "fixed": []}
        dead_uuids = set()
        dead_urls = set()

        for i, station in enumerate(stations):
            uuid = station["stationuuid"]
            name = station["name"]
            url = station["url_resolved"]
            total += 1

            status, info = check_stream(url)
            print(f"  [{i+1}/{len(stations)}] {name[:40]} → {status}" + (f" ({info})" if status != "alive" else ""))

            station_report = {"uuid": uuid, "name": name, "status": status}
            if status == "alive":
                alive_count += 1
            elif status == "dead":
                dead_count += 1
                station_report["reason"] = info
                dead_uuids.add(uuid)
                dead_urls.add(norm_url(url))
            else:
                timeout_count += 1
                station_report["reason"] = info
                dead_uuids.add(uuid)
                dead_urls.add(norm_url(url))

            shelf_report["stations"].append(station_report)

        # auto-fix: 替换失效台
        if auto_fix and dead_uuids:
            print(f"  → 开始自动替换 {len(dead_uuids)} 个失效台...")
            new_stations = []
            for s in stations:
                if s["stationuuid"] in dead_uuids:
                    print(f"  → 替换: {s['name'][:40]}")
                    replacement = find_replacement(shelf_name, dead_uuids, dead_urls)
                    if replacement:
                        print(f"    ✓ 替换为: {replacement['name'][:40]}")
                        new_stations.append(replacement)
                        shelf_report["fixed"].append({
                            "replaced": s["name"],
                            "replaced_uuid": s["stationuuid"],
                            "new": replacement["name"],
                            "new_uuid": replacement["stationuuid"]
                        })
                    else:
                        print(f"    ✗ 未找到可用替换，保留原条目")
                        new_stations.append(s)
                else:
                    new_stations.append(s)
            # 回写文件
            shelf_data["stations"] = new_stations
            shelf_data["generated"] = time.strftime("%Y-%m-%d")
            shelf_data["health_checked"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with open(shelf_file, "w", encoding="utf-8") as f:
                json.dump(shelf_data, f, ensure_ascii=False, indent=2)
            print(f"  → 已保存修复后的 {shelf_name}.json")
        elif not auto_fix and dead_uuids:
            print(f"  ⚠ 发现 {len(dead_uuids)} 个失效台，使用 --auto-fix 自动替换")

        report["shelves"].append(shelf_report)

    # 输出汇总
    print("\n" + "=" * 60)
    print(f"检测完成: 共 {total} 台")
    print(f"  ✅ 存活: {alive_count}")
    print(f"  ❌ 失效: {dead_count}")
    print(f"  ⚠️ 超时: {timeout_count}")
    print(f"  可用率: {alive_count/total*100:.1f}%" if total > 0 else "  可用率: N/A")
    print("=" * 60)

    report["summary"] = {
        "total": total,
        "alive": alive_count,
        "dead": dead_count,
        "timeout": timeout_count,
        "availability": round(alive_count / total * 100, 1) if total > 0 else 0
    }

    # 保存报告
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, "latest-report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {report_path}")

    return report

# ======================== 主流程 ========================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BB Radio 货架精选池管理")
    parser.add_argument("--health-check", action="store_true", help="运行健康检查模式")
    parser.add_argument("--auto-fix", action="store_true", help="健康检查时自动替换失效台")
    args = parser.parse_args()

    if args.health_check:
        health_check(auto_fix=args.auto_fix)
    else:
        # 原始精选池生成流程
        os.makedirs(OUT_DIR, exist_ok=True)
        ADULT_BLACKLIST = load_adult_blacklist()
        print(f"成人黑名单: {len(ADULT_BLACKLIST)} 台")

        index = []
        claimed_urls = set()
        for name, params in SHELVES:
            try:
                existing_pinned = load_existing_pinned(name)
                pool = curate(name, params, existing_pinned)

                # 跨货架去重
                deduped = []
                for s in pool:
                    u = norm_url(s["url_resolved"])
                    if u in claimed_urls:
                        print(f"  跨货架重复，剔除: {s['name'][:40]}")
                        continue
                    claimed_urls.add(u)
                    deduped.append(s)
                pool = deduped

                # 恢复人工置顶
                pinned_uuids = {s["stationuuid"] for s in pool}
                for p in existing_pinned:
                    if p["stationuuid"] not in pinned_uuids \
                       and not is_commercial_source(p["name"], p["url_resolved"]):
                        p["pinned"] = True
                        pool.insert(0, p)
                        pinned_uuids.add(p["stationuuid"])
                        print(f"  恢复置顶: {p['name'][:30]}")

                with open(os.path.join(OUT_DIR, f"{name}.json"), "w", encoding="utf-8") as f:
                    json.dump({"shelf": name, "generated": time.strftime("%Y-%m-%d"),
                               "count": len(pool), "stations": pool}, f, ensure_ascii=False, indent=2)
                index.append({"shelf": name, "count": len(pool)})
                print(f"  → 已保存 {name}.json ({len(pool)} 台)")
            except Exception as e:
                print(f"  ✗ {name} 失败: {e}")
                index.append({"shelf": name, "count": 0, "error": str(e)})

        with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"shelves": index, "generated": time.strftime("%Y-%m-%d")}, f, ensure_ascii=False, indent=2)
        print("\n=== 生成完成 ===")
        print(json.dumps(index, ensure_ascii=False, indent=2))
