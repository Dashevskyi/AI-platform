# Instruction Audit Report

_Generated: 2026-05-24T05:42:42+03:00_


Total instruction snippets scanned: **244**


Similarity threshold: **0.92** (cross-tenant copies excluded)


Pairs flagged: **4806** → grouped into **8** clusters


---


## Source breakdown

| Kind | Count | Total chars |
|---|---:|---:|
| 🔧 Tenant tool | 67 | 13,996 |
| 👤 System prompt | 100 | 4,500 |
| 🔒 Hardcoded | 11 | 3,425 |
| 🛠 Builtin tool | 9 | 3,184 |
| 🔧 Tenant param | 30 | 1,971 |
| 📘 Ontology | 1 | 1,663 |
| 🛠 Builtin param | 21 | 1,409 |
| 🧠 Memory prompt | 3 | 459 |
| 📋 Rules | 2 | 216 |
| **TOTAL** | **244** | **30,823** |

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


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_dhcp_lease.params.filters`**

```
Набор фильтров по разрешённым alias, например {"client_id":"123","ip":"172.10.100.20"}
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_tasks.params.filters`**

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


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_dhcp_lease.params.query`**

```
Свободный текстовый поиск по search_columns
```


**🔧 Tenant param — `tool[403d219f-0f4a-4782-a884-0e25f8bfe241]:search_tasks.params.query`**

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


### Cluster 5 — ℹ️ Cross-tenant noise — 98 items — kinds: `system_prompt`

_Tenants involved: 98_


**👤 System prompt — `shell[d012f8ec-ee4a-42e3-a78c-c62955186e48]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[fe7a3be8-e62d-4598-ae52-e07033ba9702]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[c005de6d-782c-43ef-97e0-8f54124cc9d2]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[2f7c3735-ac3a-48a5-90fc-1a54690a135c]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[e077b4af-8472-45df-a154-350d44a522e9]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[c73b1c80-0401-4192-918c-c5eed4bd7053]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[bff2ee8b-72e1-4e93-bad2-328433ef745a]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[d9b1824f-b67c-480b-8e95-37ef9829dc6a]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[31c8369c-29c3-4b05-b3f0-185389efd811]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[c3699acf-b2db-4483-871c-a8d41c6974ae]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[b244e615-4968-44e0-afb5-1c04f9ef97e5]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[1211c83f-8baa-44cc-99a4-3a49d643a13b]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[27ef9196-e34f-4c7c-8f02-9686cad66ef3]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[7a94e812-25f4-46cc-afd8-2e832d15dcb6]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[a47bfc31-967f-47e3-b48a-a2a87eb5c5b6]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[5c29a665-9919-415f-88a8-2c2008919c67]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[a2f65941-d6ff-4fd6-b68e-1d16b651e8d2]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[4e365fe4-90ea-436a-9740-ea41305eed97]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[8a3f9d0a-f254-4134-a807-1e7aa6d85ac8]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[95b52c8e-99a3-4e6a-a68c-01173218f95a]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[760f4888-338d-411c-9367-339cf09c4c4e]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[76f314c4-bafb-4f78-9f6b-be4111d6e8f9]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[920cad30-8bd1-4a05-abef-0ce85b5dbb6b]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[999c588b-0e9a-4e55-b39d-f73f267663a6]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[2b7c7e09-b6fa-469d-893b-b61fb0923b2c]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[6045dd2c-9fd1-406e-82b7-4e9c50d74801]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[90609d9b-c5a9-4705-820e-23d111ece778]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[84a2c2d4-4a26-4ebb-9f75-1975b192f738]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[b57c43bc-acaf-4814-98e3-d280836a9ae8]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[28aedb0e-13c8-4b94-ab78-e657488693c9]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[4242daf4-9332-47e9-ac28-9011747003a6]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[38d6d850-286a-40e7-802a-a731ccfa6be8]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[b2f61b6a-9b13-4935-ade9-9cac62791438]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[35740744-e7c2-414b-b11c-46cd839ed8ed]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[d3437b31-8395-45bb-81f8-466a119522aa]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[bc630abe-f73b-4cf9-bde4-6d467b2eebb7]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[df0e4e60-9f54-453b-b775-00309df2078e]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[b4753833-1dd0-4843-bbaa-a708ea0c5cb6]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[fa45d3d3-549e-4d59-9d98-9bed59e0ccac]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[3c8748c8-af29-4a27-be2f-9581b4f8c647]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[99dac8ca-32dc-4838-9aff-a74e0ff005d8]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[ad6a8810-d9c6-40dc-9136-8d3c7b9315af]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[5d23f87f-bfc1-4737-a432-8c314f1bebf3]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[1a8ad1ef-bee1-4be5-a3d3-1018517ce17a]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[20608cc5-917c-445a-99a5-24886e2c47a7]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[050402bf-d144-4624-a5ee-e561cd8106e5]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[9e586327-e94c-4585-8273-7e6e2dc921f8]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[0f2957d7-f46c-49a2-acde-08e09fd1c18d]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[8a17ce78-c57c-4e23-bd9d-d912b1ee1149]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[861572c7-9000-4a9c-a027-b022caea6017]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[93ef4741-cc95-4849-9c4a-b25cd9dea1c8]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[750c9bfc-bf97-4d17-8af9-e073ddcddf8a]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[64000bf6-5929-465b-8a9e-96cde0eb99aa]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[d2d8234f-b980-41ff-91e9-25e3e4cf0a51]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[e95813c0-40d7-4cef-8343-76b1a2defb35]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[86773e3d-55e3-42af-97e2-92b1b5f39745]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[63296873-3cb3-44c3-8083-e9e7a4389a0e]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[5e8775a4-c41c-42fa-8f6d-ea393df36203]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[4a627a42-6dc6-4224-8aea-44475d0399c2]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[48cfd180-d471-4b57-8584-ff6a8335b7a1]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[9d2562d7-33ae-48bb-bfbc-feeadfd3d2c7]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[cd832c97-d1d7-4d75-abff-143f46e3b43d]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[4a90159b-b84a-44dd-9ebf-e49f041660b0]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[2eda1e8c-bf61-44f7-ac5e-12c1737662f1]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[b64a2d20-c477-4287-9911-72e65f248c9b]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[7c113f48-ec7d-4d08-a0a7-298178909fa2]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[4bdeab56-4ff1-4ddc-b7fd-3e5d1981ea34]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[e0803577-f65a-4679-9e7d-f00199ddc19a]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[4625bbd8-5196-4fc1-ae15-0bbc85ec756d]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[6610d71b-8990-4f44-ab5b-02c7032c5f4b]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[7c901aa4-ea0e-433f-9348-292520210eb4]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[314ef2e5-0085-4d35-862e-05a60c486437]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[1025e28d-13d1-4293-8eb3-c69bf5053bd5]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[e79e2b3e-9230-439b-9271-634fda886a3e]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[715f68fb-a458-434f-b6ff-bdb5dc592a5f]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[8703c8ac-94d7-44f2-a3e0-f1cc24c92996]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[09b8f6a0-7d49-46c9-84a5-43a2467e5bfa]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[b4659b7b-8c08-4c98-a702-4598e6de0ab6]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[7c5cc5e1-3c4b-4964-8ab4-014e647965f8]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[567ce542-e1f6-4b52-96fc-93e0d8082dde]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[a58710d8-73b9-4129-b5b5-3bdc151c4143]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[31ccbfec-3dfa-4ca7-8bb9-6ddf594effb1]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[eafa0a9d-af2e-4a25-96e9-eb3ce46643af]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[bdc45cab-57bf-45d9-997e-4662fbd965b4]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[fb08e868-2e83-4001-8b5d-be9ce05174ca]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[988386ff-e378-4021-b26c-4e71ad4a6a93]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[f19a77d9-b96e-4a0b-9d36-c6b1962a7fb7]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[e9604f0c-6cf9-4545-8138-3875c925c805]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[f3b80529-7a0e-4a6c-92c2-c3cd76f41583]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[71b08c0a-025c-4ee4-bf46-2d06d2ba98e4]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[deecbf4d-349f-4c16-9b36-b67603b4ecb7]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[10a884fa-12c7-494e-8753-a2a98476de33]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[91c33db3-638c-4476-8797-6247863cca94]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[d67e99ec-9af5-420c-99b7-f3527edbcbb0]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[90afbbc6-723a-4c1d-b861-e41f1b21d74c]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[32e81e3d-1e98-4536-8d75-ba050a69e807]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[b79be74a-ede6-4d52-bd37-059cf366eaa0]:system_prompt`**

```
You are a helpful assistant. Reply briefly.
```


**👤 System prompt — `shell[8fdc1fbf-33d5-4139-b790-a788ea75fd27]:system_prompt`**

```
You are a concise assistant. Reply briefly.
```


### Cluster 6 — ℹ️ Cross-tenant noise — 3 items — kinds: `builtin_param`


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


### Cluster 7 — ℹ️ Cross-tenant noise — 3 items — kinds: `builtin_param`


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


### Cluster 8 — ℹ️ Cross-tenant noise — 2 items — kinds: `builtin_param`


**🛠 Builtin param — `builtin:recall_chat.limit`**

```
Сколько результатов вернуть (1-20). По умолчанию 5.
```


**🛠 Builtin param — `builtin:recall_memory.limit`**

```
Сколько записей вернуть (1-20). По умолчанию 5.
```
