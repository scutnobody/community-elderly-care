"""Real filtered statistics computed by SQLite rather than Python N-by-M loops."""
from datetime import date, timedelta


def statistics(module, query):
    value=lambda key:(query.get(key) or [''])[0]
    filters={k:value(k) for k in ('date_from','date_to','community','service_type','provider_code')}
    for key in ('date_from','date_to'):
        if filters[key]:
            try:date.fromisoformat(filters[key])
            except (TypeError,ValueError):raise ValueError('筛选日期无效')
    if filters['date_from'] and filters['date_to'] and filters['date_from']>filters['date_to']:
        raise ValueError('开始日期不能晚于结束日期')
    conditions=[];params=[]
    for key,column,op in (('date_from','o.service_date','>='),('date_to','o.service_date','<='),('community','e.community','='),('service_type','s.service_type','='),('provider_code','p.provider_code','=')):
        if filters[key]:conditions.append(column+op+'?');params.append(filters[key])
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    with module.connect_db() as c:
        c.execute('BEGIN')
        c.execute('''CREATE TEMP TABLE report_orders AS SELECT o.order_id,o.status,o.service_date,o.time_slot,
            e.community,s.service_type,a.provider_id,p.provider_code,p.full_name provider_name,a.arrive_at,a.finish_at
            FROM service_orders o JOIN elderly_profiles e ON e.elder_id=o.elder_id
            JOIN service_items s ON s.service_item_id=o.service_item_id
            LEFT JOIN service_assignments a ON a.order_id=o.order_id
            LEFT JOIN service_providers p ON p.provider_id=a.provider_id'''+where,params)
        c.execute('CREATE INDEX temp.idx_report_provider ON report_orders(provider_id)')
        def scalar(sql,values=()):return c.execute(sql,values).fetchone()[0]
        def groups(table,key,condition='',values=()):
            return {str(r['key']):r['n'] for r in c.execute(f"SELECT COALESCE(NULLIF({key},''),'未填写') key,COUNT(*) n FROM {table} {condition} GROUP BY 1",values)}
        counts=dict(c.execute("""SELECT COUNT(*) order_count,COALESCE(SUM(status='PENDING_ASSIGN'),0) pending_orders,
            COALESCE(SUM(status='COMPLETED'),0) completed,COALESCE(SUM(status!='CANCELLED'),0) denominator FROM report_orders""").fetchone())
        elder_where='WHERE is_deleted=0'+(' AND community=?' if value('community') else '')
        elder_values=(value('community'),) if value('community') else ()
        elder_count=scalar('SELECT COUNT(*) FROM elderly_profiles '+elder_where,elder_values)
        today=date.today();today_text=today.isoformat();week=(today-timedelta(days=today.weekday())).isoformat()
        age_sql="""CASE WHEN birth_date IS NULL OR birth_date='' OR date(birth_date) IS NULL THEN '未填写'
            WHEN CAST((julianday(?) - julianday(birth_date))/365.2425 AS INTEGER)<60 THEN '60岁以下'
            WHEN CAST((julianday(?) - julianday(birth_date))/365.2425 AS INTEGER)<70 THEN '60—69岁'
            WHEN CAST((julianday(?) - julianday(birth_date))/365.2425 AS INTEGER)<80 THEN '70—79岁'
            ELSE '80岁及以上' END"""
        age_groups=groups('elderly_profiles',age_sql,elder_where,(today_text,today_text,today_text,*elder_values))
        for key in ('60岁以下','60—69岁','70—79岁','80岁及以上','未填写'):age_groups.setdefault(key,0)
        rated=dict(c.execute('''SELECT COUNT(*) n,SUM((r.attitude_score+r.quality_score+r.punctual_score)/3.0>=4) good
            FROM service_reviews r JOIN report_orders o ON o.order_id=r.order_id''').fetchone())
        # Aggregate service records once. Legacy duplicates cannot multiply order counts.
        c.execute('''CREATE TEMP TABLE report_hours AS SELECT r.order_id,
            MAX(CASE WHEN julianday(r.actual_end_time)>=julianday(r.actual_start_time)
                THEN (julianday(r.actual_end_time)-julianday(r.actual_start_time))*24 END) hours
            FROM service_records r JOIN report_orders o ON o.order_id=r.order_id GROUP BY r.order_id''')
        c.execute('CREATE UNIQUE INDEX temp.idx_report_record_order ON report_hours(order_id)')
        performance=[dict(r) for r in c.execute('''
            WITH job AS (
                SELECT o.*,h.hours, r.review_id,(r.attitude_score+r.quality_score+r.punctual_score)/3.0 score,
                    EXISTS(SELECT 1 FROM service_complaints sc WHERE sc.order_id=o.order_id) complained
                FROM report_orders o LEFT JOIN report_hours h ON h.order_id=o.order_id
                LEFT JOIN service_reviews r ON r.order_id=o.order_id
            ) SELECT p.provider_code,p.full_name,
                COUNT(CASE WHEN j.status IN ('PENDING_REVIEW','COMPLETED') THEN 1 END) orders,
                ROUND(COALESCE(SUM(CASE WHEN j.status IN ('PENDING_REVIEW','COMPLETED') THEN
                    COALESCE(j.hours,CASE WHEN julianday(j.finish_at)>=julianday(j.arrive_at)
                    THEN (julianday(j.finish_at)-julianday(j.arrive_at))*24 END,0) ELSE 0 END),0),2) hours,
                ROUND(AVG(j.score),2) average_score,
                ROUND(100.0*SUM(CASE WHEN j.score>=4 THEN 1 ELSE 0 END)/NULLIF(COUNT(j.review_id),0),1) good_rate,
                ROUND(100.0*SUM(CASE WHEN j.status IN ('PENDING_REVIEW','COMPLETED') AND j.complained THEN 1 ELSE 0 END)
                    /NULLIF(COUNT(CASE WHEN j.status IN ('PENDING_REVIEW','COMPLETED') THEN 1 END),0),1) complaint_rate,
                COUNT(CASE WHEN j.service_date=? AND j.status!='CANCELLED' THEN 1 END) today_orders,
                COUNT(CASE WHEN j.service_date BETWEEN ? AND ? AND j.status!='CANCELLED' THEN 1 END) week_orders
                FROM service_providers p LEFT JOIN job j ON j.provider_id=p.provider_id
                WHERE p.is_deleted=0 GROUP BY p.provider_id ORDER BY p.provider_id
            ''',(today_text,week,(today+timedelta(days=6-today.weekday())).isoformat()))]
        econditions=[];eparams=[]
        for key,col,op in (('date_from','date(q.created_at)','>='),('date_to','date(q.created_at)','<='),('community','e.community','=')):
            if value(key):econditions.append(col+op+'?');eparams.append(value(key))
        c.execute('CREATE TEMP TABLE report_emergencies AS SELECT q.* FROM emergency_requests q JOIN elderly_profiles e ON e.elder_id=q.elder_id'+(' WHERE '+' AND '.join(econditions) if econditions else ''),eparams)
        emergency=dict(c.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(status IN ('待调度','已派单','服务人员已出发','已到达')),0) active,
            ROUND(AVG(CASE WHEN julianday(dispatch_at)>=julianday(created_at) THEN (julianday(dispatch_at)-julianday(created_at))*86400 END),1) response,
            ROUND(AVG(CASE WHEN julianday(arrive_time)>=julianday(created_at) THEN (julianday(arrive_time)-julianday(created_at))*86400 END),1) arrival,
            ROUND(100.0*SUM(status='误报取消' OR result='误报')/NULLIF(COUNT(*),0),1) false_rate FROM report_emergencies""").fetchone())
        aconditions=[];aparams=[]
        for key,col,op in (('date_from','date(a.created_at)','>='),('date_to','date(a.created_at)','<='),('community','e.community','=')):
            if value(key):aconditions.append(col+op+'?');aparams.append(value(key))
        alert_groups={r['metric_type']:r['n'] for r in c.execute('SELECT a.metric_type,COUNT(*) n FROM health_alerts a JOIN elderly_profiles e ON e.elder_id=a.elder_id'+(' WHERE '+' AND '.join(aconditions) if aconditions else '')+' GROUP BY a.metric_type',aparams)}
        cohort_where=elder_where+" AND elder_tags LIKE '%慢病%'"
        cohort=scalar('SELECT COUNT(*) FROM elderly_profiles '+cohort_where,elder_values)
        health_conditions=["e.is_deleted=0","e.elder_tags LIKE '%慢病%'"];health_params=[]
        if value('community'):health_conditions.append('e.community=?');health_params.append(value('community'))
        if value('date_from'):health_conditions.append('v.measured_at>=?');health_params.append(value('date_from'))
        if value('date_to'):health_conditions.append('v.measured_at<?');health_params.append((date.fromisoformat(value('date_to'))+timedelta(days=1)).isoformat())
        visit_conditions=[clause.replace('v.measured_at','v.visit_date') for clause in health_conditions]
        covered=scalar('SELECT COUNT(*) FROM (SELECT v.elder_id FROM health_vitals v JOIN elderly_profiles e ON e.elder_id=v.elder_id WHERE '+' AND '.join(health_conditions)+' UNION SELECT v.elder_id FROM visit_records v JOIN elderly_profiles e ON e.elder_id=v.elder_id WHERE '+' AND '.join(visit_conditions)+')',(*health_params,*health_params))
        result={'elder_count':elder_count,'provider_count':scalar('SELECT COUNT(*) FROM service_providers WHERE is_deleted=0'),
            'institution_count':scalar('SELECT COUNT(*) FROM institutions'),'order_count':counts['order_count'],'pending_orders':counts['pending_orders'],
            'active_emergencies':emergency['active'],'completion_rate':round(counts['completed']/counts['denominator']*100,1) if counts['denominator'] else None,
            'good_rate':round((rated['good'] or 0)/rated['n']*100,1) if rated['n'] else None,
            'order_trend':[dict(r) for r in c.execute("SELECT substr(service_date,1,7) month,COUNT(*) orders,SUM(status='COMPLETED') completed FROM report_orders GROUP BY 1 ORDER BY 1")],
            'service_type_counts':groups('report_orders','service_type'),'community_counts':groups('report_orders','community'),
            'time_slot_counts':groups('report_orders','time_slot'),'age_groups':age_groups,
            'gender_groups':groups('elderly_profiles','gender',elder_where,elder_values),
            'self_care_groups':groups('elderly_profiles','self_care_level',elder_where,elder_values),
            'living_groups':groups('elderly_profiles','living_situation',elder_where,elder_values),'provider_performance':performance,
            'health_alert_count':sum(alert_groups.values()),'health_alert_groups':alert_groups,
            'chronic_coverage_rate':round(covered/cohort*100,1) if cohort else None,
            'emergency_count':emergency['n'],'emergency_type_counts':groups('report_emergencies','emergency_type'),
            'emergency_result_counts':groups('report_emergencies','result'),'average_response_seconds':emergency['response'],
            'average_arrival_seconds':emergency['arrival'],'false_alarm_rate':emergency['false_rate'],'as_of':today_text,'filters':filters,
            'source':'真实数据库记录（含明确保留的课程样本）',
            'notes':'老人结构按有效档案及社区；交易按日期/社区/类别/人员筛选；响应=登记至首次派单；好评=三项平均≥4；缺失不按零；慢病覆盖按慢病标签及期内健康或就诊登记，按老人去重。'}
    return result
