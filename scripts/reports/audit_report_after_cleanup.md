# Instruction Audit Report

_Generated: 2026-05-24T05:56:51+03:00_


Total instruction snippets scanned: **147**


Similarity threshold: **0.92** (cross-tenant copies excluded)


Pairs flagged: **53** → grouped into **7** clusters


---


## Source breakdown

| Kind | Count | Total chars |
|---|---:|---:|
| 🔧 Tenant tool | 67 | 13,996 |
| 🔒 Hardcoded | 11 | 3,425 |
| 🛠 Builtin tool | 9 | 3,184 |
| 🔧 Tenant param | 30 | 1,971 |
| 📘 Ontology | 1 | 1,663 |
| 🛠 Builtin param | 21 | 1,409 |
| 🧠 Memory prompt | 3 | 459 |
| 👤 System prompt | 3 | 329 |
| 📋 Rules | 2 | 216 |
| **TOTAL** | **147** | **26,652** |

## Clusters (>= 0.92), sorted by priority


_Priority: HC-overlap → same-tenant drift → mixed-kind → other._


### Cluster 1 — ⚠️ Same-tenant drift — 9 items — kinds: `tool_param`

_Tenants involved: 1_


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:topology_neighbors.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:topology_subtree.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:dhcp_vlan_pool.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:switch_lldp_neighbors.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:switch_commands_list.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:pon_path.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:switch_command.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:pon_tree.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:switch_port_stats.params.path_values`**

```
Значения для path-плейсхолдеров endpoint
```


### Cluster 2 — ⚠️ Same-tenant drift — 4 items — kinds: `tool_param`

_Tenants involved: 1_


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_dev_by_mac.params.filters`**

```
Набор фильтров по разрешённым alias, например {"client_id":"123","ip":"172.10.100.20"}
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_tasks.params.filters`**

```
Набор фильтров по разрешённым alias, например {"client_id":"123","ip":"172.10.100.20"}
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_dhcp_lease.params.filters`**

```
Набор фильтров по разрешённым alias, например {"client_id":"123","ip":"172.10.100.20"}
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_equipment_ports_info.params.filters`**

```
Набор фильтров по разрешённым alias, например {"client_id":"123","ip":"172.10.100.20"}
```


### Cluster 3 — ⚠️ Same-tenant drift — 3 items — kinds: `tool_param`

_Tenants involved: 1_


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_dev_by_mac.params.query`**

```
Свободный текстовый поиск по search_columns
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_tasks.params.query`**

```
Свободный текстовый поиск по search_columns
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_dhcp_lease.params.query`**

```
Свободный текстовый поиск по search_columns
```


### Cluster 4 — ⚠️ Same-tenant drift — 2 items — kinds: `tool_desc`

_Tenants involved: 1_


**🔧 Tenant tool — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:send_telegram_message`**

```
Отправляет сообщение в Telegram через Bot API (метод sendMessage). Используй для уведомлений админам о критических событиях, авариях, результатах диагностики, либо когда пользователь явно попросил отправить сообщение в Telegram. Параметры: chat_id (id чата/пользователя/группы; для группового чата начинается с минуса), text (текст сообщения, поддерживает HTML-разметку: <b>, <i>, <u>, <s>, <code>, <pre>, <a href='...'>). Длина text — до 4096 символов.
```


**🔧 Tenant tool — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:send_telegram_message.function.description`**

```
Отправить сообщение в Telegram через Bot API (sendMessage). Используй для уведомлений админам о критических событиях, авариях, либо когда пользователь явно попросил отправить в Telegram. text поддерживает HTML: <b>, <i>, <u>, <s>, <code>, <pre>, <a href=...>. Лимит 4096 симв.
```


### Cluster 5 — ℹ️ Cross-tenant noise — 3 items — kinds: `builtin_param`


**🛠 Builtin param — `builtin:recall_chat.query`**

```
Семантический запрос (1-15 слов).
```


**🛠 Builtin param — `builtin:search_kb.query`**

```
Семантический запрос (1-15 слов).
```


**🛠 Builtin param — `builtin:recall_memory.query`**

```
Семантический запрос (1-15 слов).
```


### Cluster 6 — ℹ️ Cross-tenant noise — 3 items — kinds: `builtin_param`


**🛠 Builtin param — `builtin:find_artifacts.scope`**

```
chat — только этот чат (default); tenant — все чаты (если разрешено).
```


**🛠 Builtin param — `builtin:recall_chat.scope`**

```
chat — только этот чат (default); tenant — все чаты (если включено политикой).
```


**🛠 Builtin param — `builtin:recall_memory.scope`**

```
chat — только этот чат (default); tenant — вся память тенанта (если разрешено политикой).
```


### Cluster 7 — ℹ️ Cross-tenant noise — 2 items — kinds: `builtin_param`


**🛠 Builtin param — `builtin:recall_chat.limit`**

```
Сколько результатов вернуть (1-20). По умолчанию 5.
```


**🛠 Builtin param — `builtin:recall_memory.limit`**

```
Сколько записей вернуть (1-20). По умолчанию 5.
```
