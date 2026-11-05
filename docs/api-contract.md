# Acceptance extension contract

All extension routes use `/api/acceptance`; existing endpoints stay supported. JSON error mapped by core to 400/403/409, never success-only placeholders. GET list -> `{ok:true,items:[],total:N,page:1,page_size:50}`; POST create/update -> `{ok:true,item:{...},id:N}`. POST with `id` updates existing resource. Query includes page/page_size (max 200), q, date_from/date_to and resource filters. `elder_code` for admin health lists; elderly users always scoped to themselves. Unknown extension route must 404.

Business module `migrate(module)` runs after original initialize_database; `route(handler,module,method,path,query)->bool` handles extension endpoints. Handler user role is admin/elder/provider/guest, codes elder_code/provider_code. Transactions and ownership checks required. No changes to root-owned core/server.

## Tables shared with core

Extend service_items: content TEXT default '', service_mode TEXT default '上门', target_group TEXT default '', booking_notes TEXT default '', required_certificate TEXT default '', service_status TEXT default '正常提供'. Seed metadata from known existing project service categories; don't fabricate approved credentials.
Extend service_orders: start_at TEXT, end_at TEXT, service_name_snapshot TEXT, price_snapshot REAL, duration_snapshot INTEGER, condition_check TEXT default ''.
Extend service_records: medical_details TEXT default '', rehab_details TEXT default '', materials_json TEXT default '[]', attachments_json TEXT default '[]', review_status TEXT default '待审核', review_note TEXT default '', reviewed_by TEXT, reviewed_at TEXT. Legacy reviewed orders' records -> 历史导入.
Extend elderly_profiles: photo_attachment_id INTEGER, archive_reason TEXT default '', archived_at TEXT.

New shared tables:
- provider_credentials(credential_id PK,provider_id FK,name,certificate_no,expires_on,status PENDING/APPROVED/REJECTED,attachment_id,review_note,reviewed_by,reviewed_at,created_at). Legacy strings -> PENDING, expiry safely parsed or null; null expiry not eligible until updated.
- provider_locations(provider_id PK,latitude REAL,longitude REAL,recorded_at TEXT).
- health_thresholds(threshold_id PK,elder_id FK,metric TEXT,lower_bound REAL,upper_bound REAL,enabled INTEGER,action TEXT, UNIQUE(elder_id,metric)). Metrics systolic,diastolic,glucose,heart_rate,temperature,weight,sleep_hours; NULL boundary allowed, at least one when enabled.
- notifications(notification_id PK,recipient_role TEXT,recipient_code TEXT default '',kind TEXT,title TEXT,body TEXT,reference TEXT,is_read INTEGER default 0,created_at TEXT). recipient_code '' means all accounts of that role (admin), provider_code or elder_code otherwise.
- external_notifications(notification_id PK,emergency_id FK,target TEXT,channel TEXT,status TEXT,created_at TEXT). Channel status UNCONFIGURED/SIMULATED_CONFIRMED, never fake delivered.
- business_logs(log_id PK,entity_type TEXT,entity_key TEXT,action TEXT,actor TEXT,detail TEXT,created_at TEXT).
- attachments(attachment_id PK,owner_type TEXT,owner_key TEXT,purpose TEXT,file_name TEXT,stored_name TEXT,mime_type TEXT,file_size INTEGER,uploaded_by TEXT,created_at TEXT).
- assignment_history(history_id PK,order_id FK,provider_id FK,action TEXT,reason TEXT,actor TEXT,created_at TEXT).
Other business tables names/columns owned by business implementation; frontend uses API payloads below, not direct DB.

## API resources

- GET/POST institutions: institution_id/id, institution_code,name,institution_type,province,city,district,street,community,address,phone,bed_count,introduction,service_ids array,attachment_ids array. GET public-to-authenticated all roles; POST admin.
- GET/POST services: id/service_item_id,service_code,service_name,service_type,price,duration_minutes,content,service_mode,target_group,booking_notes,required_certificate,service_status. GET all roles, POST admin. Map service_status paused -> enabled=0.
- GET/POST packages: id,name,price,valid_until,status,items:[{service_item_id,frequency}]. GET all roles POST admin.
- GET/POST medications: id,elder_code,name,spec,usage,scheduled_at,status (已服/未服/漏服). GET/POST admin or own elder.
- GET/POST visits: id,elder_code,visit_date,hospital,department,diagnosis,advice,prescription,follow_up. GET/POST admin or own elder.
- GET/POST thresholds: id/threshold_id,elder_code,metric,lower_bound,upper_bound,enabled,action. GET/POST admin or own elder.
- GET/POST credentials: id/credential_id,provider_code,name,certificate_no,expires_on,attachment_id. Provider may submit pending only. POST /credentials/{id}/review admin body {status:'APPROVED'|'REJECTED',review_note,...} requires meaningful expiry/name/number on approval. GET admin/provider own.
- GET/POST training: id,provider_code,name,training_date,result,service_type,service_years. Admin/provider own; service type years cannot grant authority without credential review.
- GET records: joined service_records,order_no,elder_name,provider_name. Admin/all or scoped provider/elder. POST /records/{id}/review admin {status:'已通过'|'已退回',review_note}.
- POST /complaints/{complaint_no}/handle admin {admin_reply,status:'已处理'}.
- GET notifications: recipient scope, ?after_id=0; POST /notifications/{id}/read own recipients only.
- POST /locations provider own {latitude,longitude}; GET /locations admin. Record current server timestamp.
- POST /emergencies/{emergency_no}/confirm admin {note}, records SIMULATED_CONFIRMED and business log; existing request must exist.
- POST /elders/{elder_code}/archive admin {reason} checks active orders/emergencies, records reason and soft deletion.
- POST attachments: {owner_type:'elder'|'provider'|'institution'|'order'|'complaint',owner_key:businessCodeOrId,purpose:'portrait'|'certificate'|'facility'|'record'|'review'|'complaint',file_name,mime_type,file_data:base64}; returns item with attachment_id. GET /attachments?owner_type=...&owner_key=... returns authorized list; GET /attachments/{id}/download streams bytes. Actual file signature/type checked <=12MB, safe randomized filenames under module.BASE_DIR/uploads/attachments. Only admin can institution/elder portraits; elderly own order review/complaint; assigned provider order record/provider certificates.

## Core owned routes and hooks

- Existing create_order validates standardized intervals and conditional meal/medical fields. `condition_check` saved when supplied and displayed in audit UI. Core computes snapshot fields.
- Existing assignment checks APPROVED nonexpired provider_credentials matching services.required_certificate; legacy pending does not pass. Generic no-required-certificate service only type/area active rules.
- Existing create_service_record accepts medical_details,rehab_details,materials_json,attachments_json; returned records expose review fields. Returned record can be resubmitted; approved record immutable.
- Existing health-records only returns actual measurements, all samples in range. Fixed thresholds used only when no personal row; configured disabled row suppresses that metric's alert.
- Existing /api/dashboard-stats returns `{ok:true,stats:{elder_count,order_count,provider_count,institution_count,pending_orders,active_emergencies,order_trend:[{month,orders,completed}],service_type_counts,as_of,...}}` plus GET /api/acceptance/statistics same real filtered stats richer groups. Root handles stats path before business dispatch.
- Existing /api/reports/export?format=pdf|excel&date_from=...&date_to=...&community=...&service_type=... exports matching real filtered data.
- Existing emergency create immediately inserts notifications for admin and elder, external rows UNCONFIGURED; dispatch also notifies assigned provider. Frontend posts before geolocation; may use POST /api/acceptance/emergencies/{id}/location for owner coordinate update if business implements it.
- Core accepts GET /api/acceptance/device-info and POST /api/acceptance/device-import (elder/admin authorized elder_code,device_code,external_id,measurement object), response clearly simulated; de-dup via device_imports table root owned migration.
- Core existing /api/orders/{token}/reassign POST admin {provider_code,reason} pending-service only, atomic eligibility; /abort POST admin {reason} service in progress -> cancelled with logs.
- POST /api/orders/{token}/exception admin/assigned provider {completion_status:'未执行'|'部分完成',reason}. Requires an assigned active task; partial execution requires arrival. Creates a pending-review record and ends the assignment. Unexecuted records keep NULL actual start/end, including returned-record resubmission.
- Appointment `time_slot` supports explicit next-day text such as `09:00-次日09:00`; duration and selected interval are positive and at most 1440 minutes. Conflicts compare normalized full timestamps across calendar days.
- GET /api/orders/{token} additionally returns order price snapshot and a role-authorized `care_precautions` projection (allergies, history, self-care, diet, communication and emergency contact). Identity/insurance document numbers are excluded from that projection.
- GET /api/acceptance/elders/{code}/history admin/own elder returns profile and separately paged orders, health, emergencies and records with per-category totals.
- GET /api/acceptance/my-statistics provider only accepts date_from/date_to and forces the authenticated provider; global population and other personnel are excluded.

Frontend must not send notification delivery claims, invent sample statistics, or rely on max 200 paginated rows for all-data totals.
