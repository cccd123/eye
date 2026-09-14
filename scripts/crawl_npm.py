# crawl_npm.py - 台北故宫Open Data雕刻类文物完整爬取
import requests, json, time, os, re
from bs4 import BeautifulSoup

SAVE_DIR = r"D:\MuseumGlasses\knowledge_base\raw_text"
os.makedirs(SAVE_DIR, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Referer": "https://digitalarchive.npm.gov.tw/opendata/",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://digitalarchive.npm.gov.tw"
})

SEARCH_URL  = "https://digitalarchive.npm.gov.tw/opendata/Pub/Search"
DETAIL_BASE = "https://digitalarchive.npm.gov.tw"

def fetch_list_page(page):
    """获取第page页的文物列表，返回 [(id, name), ...]"""
    payload = {
        "RegisterType": "雕刻",
        "RegisterTypeEng": None,
        "IndexYear": None,
        "SearchContent": "",
        "WestBeginYear": 0,
        "WestEndYear": 0,
        "YearDisplay": "",
        "PageInfo": {"PageIndex": page, "PageSize": 15, "PageCount": 0}
    }
    try:
        r = SESSION.post(SEARCH_URL, json=payload, timeout=20)
        soup = BeautifulSoup(r.text, "lxml")
        items = []
        for a in soup.select("a[href*='/opendata/Pub/Detail/']"):
            href = a.get("href", "")
            name = a.get_text(strip=True)
            # 从href提取ID，如 /opendata/Pub/Detail/1828?dep=U&mode=full -> 1828
            m = re.search(r"/Detail/(\d+)", href)
            if m and name and name != "open_in_new":
                items.append((m.group(1), name))
        return items
    except Exception as e:
        print(f"  第{page}页列表失败: {e}")
        return []

def fetch_detail(artifact_id, name):
    """获取单件文物详情页，提取完整信息"""
    url = f"{DETAIL_BASE}/opendata/Pub/Detail/{artifact_id}?dep=U&mode=full"
    try:
        r = SESSION.get(url, timeout=20)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "lxml")

        # ── 提取中文名称（页面h1或大标题）
        name_tag = soup.find("h1") or soup.find("h2")
        full_name = name_tag.get_text(strip=True) if name_tag else name

        # ── 提取说明文字（正文介绍段落）
        desc = ""
        # 台北故宫详情页说明通常在 .description 或特定div里
        for sel in [".description", ".intro", ".artifact-desc",
                    "[class*='desc']", "[class*='intro']"]:
            tag = soup.select_one(sel)
            if tag:
                desc = tag.get_text(strip=True)
                break
        # 备用：找最长的段落文字
        if not desc:
            paras = [p.get_text(strip=True) for p in soup.find_all("p")
                     if len(p.get_text(strip=True)) > 40]
            if paras:
                desc = max(paras, key=len)

        # ── 提取属性表（时代、尺寸、文物号等）
        attrs = {}
        # 方式1：表格
        for tr in soup.select("table tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                k = tds[0].get_text(strip=True)
                v = tds[1].get_text(strip=True)
                if k and v:
                    attrs[k] = v
        # 方式2：定义列表 dl/dt/dd
        dts = soup.find_all("dt")
        for dt in dts:
            dd = dt.find_next_sibling("dd")
            if dd:
                attrs[dt.get_text(strip=True)] = dd.get_text(strip=True)
        # 方式3：.info类div
        for row in soup.select(".info-item, .meta-item, [class*='info'] li"):
            text = row.get_text(strip=True)
            for sep in ["：", ":", "｜"]:
                if sep in text:
                    parts = text.split(sep, 1)
                    attrs[parts[0].strip()] = parts[1].strip()
                    break

        return {
            "id": artifact_id,
            "name": full_name or name,
            "description": desc,
            "attributes": attrs,
            "url": url,
            "source": "国立故宫博物院（台北）Open Data CC BY 4.0"
        }
    except Exception as e:
        print(f"  详情获取失败 id={artifact_id}: {e}")
        return None

def main():
    all_artifacts = []
    seen_ids = set()
    total_pages = 45  # 667件 / 15 = 45页

    print("=" * 50)
    print(f"开始爬取台北故宫雕刻类文物（共约667件，{total_pages}页）")
    print("=" * 50)

    for page in range(1, total_pages + 1):
        print(f"\n📄 第{page}/{total_pages}页列表...")
        items = fetch_list_page(page)

        if not items:
            print("  此页无数据，可能已到末尾")
            break

        print(f"  找到 {len(items)} 件")

        for artifact_id, name in items:
            if artifact_id in seen_ids:
                continue
            seen_ids.add(artifact_id)

            detail = fetch_detail(artifact_id, name)
            if detail:
                all_artifacts.append(detail)
                desc_len = len(detail.get("description", ""))
                print(f"    ✅ {detail['name'][:25]} | 说明字数:{desc_len} | 属性:{len(detail['attributes'])}项")
            else:
                print(f"    ⚠️  {name[:25]} 获取失败")

            time.sleep(0.8)  # 礼貌间隔，防止被封

        # 每页之间稍作停顿
        time.sleep(1.5)

        # 每10页保存一次（防止中途崩溃丢数据）
        if page % 10 == 0:
            tmp_path = os.path.join(SAVE_DIR, "npm_artifacts_tmp.json")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(all_artifacts, f, ensure_ascii=False, indent=2)
            print(f"  💾 已临时保存 {len(all_artifacts)} 件")

    # 最终保存
    save_path = os.path.join(SAVE_DIR, "npm_artifacts.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(all_artifacts, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"✅ 爬取完成！共 {len(all_artifacts)} 件文物")
    print(f"保存路径: {save_path}")

    # 质量检查：打印前3件预览
    print("\n--- 数据预览（前3件）---")
    for a in all_artifacts[:3]:
        print(f"名称：{a['name']}")
        print(f"说明：{a['description'][:100]}...")
        print(f"属性：{a['attributes']}")
        print()

    # 统计说明文字覆盖率
    has_desc = sum(1 for a in all_artifacts if len(a.get("description","")) > 20)
    print(f"有说明文字的文物：{has_desc}/{len(all_artifacts)} ({100*has_desc//max(len(all_artifacts),1)}%)")

if __name__ == "__main__":
    main()