"""Compact Notion overview; detailed records stay in a child page."""
from collections import Counter
from io import BytesIO
from pathlib import Path

from ..domain.statistics import plain, property_text
from ..storage.repository import digest
from .schema import rich
from .bootstrap import MARKER, REPORT_INTRO

SYNC_URL = 'https://deployment-sync-qa.nearfinch1.chatgpt.site'
CAPTION = '서비스·대표 영역별 배포 비중 (전체 등록 공지 기준)'


def chart_png(statistics, pages):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt, font_manager
    for path in ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/System/Library/Fonts/AppleSDGothicNeo.ttc']:
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
            plt.rcParams['font.family'] = font_manager.FontProperties(fname=path).get_name()
            break
    # Primary Component is mutually exclusive; multi-select Components must not be used for pie shares.
    by_key = {property_text(p['properties'], 'Source Key').strip() or 'page:' + p['id']: p for p in sorted(pages, key=lambda p: (p.get('last_edited_time', ''), p['id'])) if not p.get('archived') and not p.get('in_trash')}
    areas = Counter()
    for record in statistics.records:
        name = property_text(by_key[record.key]['properties'], 'Component') or 'ETC'
        areas['NATIVE' if name.startswith('NATIVE') else name] += 1
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), facecolor='white')
    colors = ['#4F70F8', '#42B8A5', '#F3B34C', '#A184E8', '#EC7B95', '#AAB4C4']
    for ax, title, ranked in zip(axes, ['서비스별 배포', '대표 영역별 배포'], [statistics.ranking(statistics.records, 'service'), sorted(areas.items(), key=lambda x: (-x[1], x[0]))]):
        items = list(ranked[:5])
        if len(ranked) > 5:
            items.append(('기타', sum(n for _, n in ranked[5:])))
        if items:
            wedges, _ = ax.pie([n for _, n in items], startangle=90, counterclock=False, colors=colors, wedgeprops={'width': .35, 'edgecolor': 'white', 'linewidth': 2})
            ax.legend(wedges, [f'{name}  {n}건 ({n / len(statistics.records):.0%})' for name, n in items], loc='center left', bbox_to_anchor=(.94, .5), frameon=False, fontsize=9)
        ax.text(0, .08, str(len(statistics.records)), ha='center', va='center', fontsize=28, weight='bold', color='#25324A')
        ax.text(0, -.18, '등록 배포', ha='center', fontsize=10, color='#7A8496')
        ax.set_title(title, loc='left', fontsize=13, pad=12)
    fig.subplots_adjust(left=.025, right=.80, wspace=1.25, top=.85, bottom=.12)
    output = BytesIO()
    fig.savefig(output, format='png', dpi=160, facecolor='white', bbox_inches='tight')
    plt.close(fig)
    return output.getvalue()


def refresh_dashboard(client, repo, root, lowdata, statistics, pages):
    month = statistics.within(*statistics.interval('month', statistics.today))
    week = statistics.within(*statistics.interval('week', statistics.today))
    pending = any(j['state'] != 'Completed' for j in statistics.jobs)
    summary = f'전체 {len(statistics.records)}건 · 이번 달 {len(month)}건 · 이번 주 {len(week)}건\n이번 달 최다: {statistics.winner(month, "service")}\n등록된 공지 기준' + (' · 과거 데이터 수집 중' if pending else '')
    fingerprint = digest({'v': 1, 'records': [r.payload() for r in statistics.records], 'primary': [property_text(p['properties'], 'Component') for p in pages]})
    children = client.children(root)
    # Remove only the previous generated navigation/progress, never user content or records.
    for b in children:
        kind = b['type']
        text = plain(b.get(kind, {}).get('rich_text', []))
        if (kind == 'paragraph' and (text == REPORT_INTRO or text.startswith('📊 Summary:'))) or (kind == 'callout' and text.startswith(('📊 Summary 통계 · Deployment Reporter\n', '과거 수집 진행 · Deployment Reporter\n'))):
            client.call('DELETE', '/blocks/' + b['id'])
    children = client.children(root)
    marker = next(b for b in children if b['type'] == 'paragraph' and plain(b['paragraph']['rich_text']) == MARKER)
    after = marker['id']
    owned_images = [b for b in children if b['type'] == 'image' and plain(b['image'].get('caption', [])).startswith((CAPTION, '배포 현황 · dashboard-v1 · '))]
    image = owned_images[0] if owned_images else None
    if not image or repo.get_value('dashboard_chart:' + root) != fingerprint or plain(image['image'].get('caption', [])) != CAPTION:
        upload = client.upload_png(chart_png(statistics, pages))
        content = {'type': 'file_upload', 'file_upload': {'id': upload}, 'caption': rich(CAPTION)}
        # Notion accepts uploaded files on append, but cannot replace a hosted image in-place.
        # Create the replacement first; recovery removes only our superseded chart blocks.
        image = client.call('PATCH', '/blocks/' + root + '/children', {'after': after, 'children': [{'object': 'block', 'type': 'image', 'image': content}]}, retry_safe=False)['results'][0]
    for old in owned_images:
        if old['id'] != image['id']:
            client.call('DELETE', '/blocks/' + old['id'])
    repo.set_value('dashboard_chart:' + root, fingerprint)
    after = image['id']
    # Stable, concise labels also serve as recovery markers after state-cache loss.
    for label, content, icon in [('요약', rich('요약\n' + summary), '📊'), ('동기화', [*rich('동기화\n'), {'type': 'text', 'text': {'content': '🔄 동기화 / 과거 수집 기간 선택', 'link': {'url': SYNC_URL}}}, *rich('\n최근 동기화 또는 과거 기간을 선택해 요청하세요.')], '🔄')]:
        existing = next((b for b in children if b['type'] == 'callout' and plain(b['callout']['rich_text']).startswith(label + '\n')), None)
        body = {'rich_text': content, 'icon': {'type': 'emoji', 'emoji': icon}}
        if existing:
            from .statistics import cell_signature
            if cell_signature([existing['callout']['rich_text']]) != cell_signature([content]):
                client.call('PATCH', '/blocks/' + existing['id'], {'callout': body})
            after = existing['id']
        else:
            result = client.call('PATCH', '/blocks/' + root + '/children', {'after': after, 'children': [{'object': 'block', 'type': 'callout', 'callout': body}]}, retry_safe=False)
            after = result['results'][0]['id']
