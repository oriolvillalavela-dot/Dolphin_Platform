
# Chem Inventory + HTE Plate Designer (v9)

**Fixes**
- Fixed a JavaScript syntax error that could stop *all buttons* and the Plate Designer from working.
- App JS loads at end of `<body>` for safe initialization.
- Plate Designer initializes on `window.load` and renders reliably.
- Robust fetch handling: parse text first, then JSON — avoids “Unexpected token <” when servers return HTML error pages.
- Manage Batch asks for **dd/mm/yyyy** when setting **Stock solution → Available**, validates on server.
- New Chemical uses dropdown for **aggregate_state**.
- Sticky "Generate bottle" column remains.

**Run**
```bash
docker build -t chemreg_v9 .
docker run --rm -p 8000:8000   -e DB_NAME=$DB_NAME -e DB_HOST=$DB_HOST -e DB_PORT=$DB_PORT   -e DB_USER=$DB_USER -e DB_PASSWORD=$DB_PASSWORD   chemreg_v9
```
