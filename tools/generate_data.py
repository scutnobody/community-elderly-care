"""Build synthetic data in a NEW separate database; never modifies the template."""
import argparse
import json
import random
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def generate(target, *, elders=50000, providers=800, orders=300000, health_records=300000, seed=42, template=None):
    target = Path(target).resolve()
    template = Path(template or PROJECT / 'elderly_care.db').resolve()
    if target == template or PROJECT in target.parents:
        raise ValueError('Choose a separate test directory outside the application data directory')
    if elders < 1 or providers < 1 or orders < 0 or health_records < 0:
        raise ValueError('Need positive elders/providers and nonnegative record counts')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwrite, even when another invocation races us.
    with target.open('xb'):
        pass
    rng = random.Random(seed)
    started = time.perf_counter()
    with closing(sqlite3.connect(template.as_uri() + '?mode=ro', uri=True)) as source:
        ddl = source.execute("SELECT type,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END").fetchall()
    with closing(sqlite3.connect(target)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        for kind, sql in ddl:
            if kind == 'table':
                db.execute(sql)
        columns = {}
        def insert_batch(table, rows):
            if not rows:
                return
            allowed = columns.setdefault(table, {r[1] for r in db.execute('PRAGMA table_info("' + table + '")')})
            keys = [k for k in rows[0] if k in allowed]
            db.executemany('INSERT INTO "' + table + '" (' + ','.join(keys) + ') VALUES(' + ','.join('?' for _ in keys) + ')',
                           ([row[key] for key in keys] for row in rows))
            db.commit()
        def batches(table, count, factory):
            for offset in range(0, count, 1000):
                insert_batch(table, [factory(i) for i in range(offset + 1, min(count, offset + 1000) + 1)])
        communities = ['青云社区', '幸福社区', '长青社区', '安康社区']
        batches('elderly_profiles', elders, lambda i: {
            'elder_id': i, 'elder_code': 'JX202600128' if i == 1 else f'TEST-E{i:07}',
            'full_name': f'容量样本老人{i}', 'phone': f'138{i:08}', 'address': f'{communities[(i-1)%4]}{i}号（合成）',
            'community': communities[(i-1)%4], 'gender': '男' if i % 2 else '女',
            'birth_date': f'{1930 + i % 36}-01-01', 'disease_history': '高血压' if i % 3 == 0 else '',
            'living_situation': '独居' if i % 4 == 0 else '与家人同住', 'elder_tags': '容量测试样本',
            'self_care_level': '完全自理', 'service_priority': '普通', 'is_deleted': 0})
        batches('service_providers', providers, lambda i: {
            'provider_id': i, 'provider_code': 'FW20260218' if i == 1 else f'TEST-P{i:05}',
            'full_name': f'容量样本人员{i}', 'phone': f'139{i:08}', 'organization': '容量测试机构',
            'skills': '助洁', 'service_areas': '、'.join(communities), 'certificates': '',
            'employment_status': '在职', 'is_deleted': 0})
        insert_batch('service_items', [{'service_item_id': 1, 'service_code': 'LOAD-CLEAN', 'service_name': '容量测试助洁',
            'service_type': '助洁', 'price': 50, 'duration_minutes': 60, 'enabled': 1,
            'required_certificate': '', 'service_status': '正常提供'}])
        epoch = datetime(2021, 1, 1, 8)
        def order(i):
            stamp = epoch + timedelta(days=(i-1) // providers, hours=((i-1) % 3) * 3)
            return {'order_id': i, 'order_no': f'LOAD-{i:09}', 'elder_id': (i-1) % elders + 1,
                'service_item_id': 1, 'service_date': stamp.date().isoformat(), 'time_slot': f'{stamp:%H:%M}-{stamp + timedelta(hours=1):%H:%M}',
                'duration_minutes': 60, 'service_address': '合成容量测试地址', 'contact_phone': '13800000000',
                'special_requirements': '仅用于隔离容量测试', 'status': 'COMPLETED',
                'start_at': stamp.isoformat(sep=' '), 'end_at': (stamp + timedelta(hours=1)).isoformat(sep=' '),
                'service_name_snapshot': '容量测试助洁', 'price_snapshot': 50, 'duration_snapshot': 60,
                'created_at': stamp.isoformat(sep=' '), 'updated_at': (stamp + timedelta(hours=1)).isoformat(sep=' ')}
        batches('service_orders', orders, order)
        def assignment(i):
            stamp = epoch + timedelta(days=(i-1) // providers, hours=((i-1) % 3) * 3)
            return {'assignment_id': i, 'order_id': i, 'provider_id': (i-1) % providers + 1, 'assigned_by': '容量测试生成器',
                'assignment_status': 'FINISHED', 'assigned_at': stamp.isoformat(sep=' '), 'depart_at': stamp.isoformat(sep=' '),
                'arrive_at': (stamp + timedelta(minutes=10)).isoformat(sep=' '), 'finish_at': (stamp + timedelta(hours=1)).isoformat(sep=' ')}
        batches('service_assignments', orders, assignment)
        batches('service_records', orders, lambda i: {'record_id': i, 'order_id': i, 'provider_id': (i-1) % providers + 1,
            'completion_status': '全部完成', 'process_description': '合成容量测试服务记录', 'review_status': '历史导入',
            'actual_start_time': assignment(i)['arrive_at'], 'actual_end_time': assignment(i)['finish_at']})
        batches('service_reviews', orders, lambda i: {'review_id': i, 'order_id': i, 'elder_id': (i-1) % elders + 1,
            'provider_id': (i-1) % providers + 1, 'attitude_score': 4, 'quality_score': 4, 'punctual_score': 4,
            'review_text': '合成容量测试评价'})
        batches('health_vitals', health_records, lambda i: {'vital_id': i, 'elder_id': (i-1) % elders + 1,
            'measured_at': (epoch + timedelta(days=(i-1)//elders)).isoformat(sep=' '), 'systolic': rng.randrange(105, 145),
            'diastolic': rng.randrange(65, 90), 'glucose': round(rng.uniform(4.5, 7), 1), 'heart_rate': rng.randrange(60, 90),
            'temperature': 36.5, 'weight': 60, 'sleep_hours': 7, 'source': '容量测试合成'})
        for kind, sql in ddl:
            if kind == 'index':
                db.execute(sql)
        db.commit()
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Generated data failed foreign key validation')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    result = {'synthetic': True, 'seed': seed, 'database': str(target), 'elders': elders, 'providers': providers,
              'orders': orders, 'health_records': health_records, 'duration_seconds': round(time.perf_counter()-started, 3)}
    with target.with_suffix(target.suffix + '.generation.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--template')
    parser.add_argument('--elders', type=int, default=50000)
    parser.add_argument('--providers', type=int, default=800)
    parser.add_argument('--orders', type=int, default=300000)
    parser.add_argument('--health-records', type=int, default=300000)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    print(json.dumps(generate(args.database, elders=args.elders, providers=args.providers, orders=args.orders,
        health_records=args.health_records, seed=args.seed, template=args.template), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
