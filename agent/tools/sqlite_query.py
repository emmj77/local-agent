"""sqlite_query.py — Outil SQLite LECTURE SEULE unique de Local_Agent.

Fusionne les anciens run_sql_query (supprimé) et le serveur MCP mcp_sqlite_unified
(supprimé) : une seule surface SQL, toujours en lecture seule, jamais de R/W.

Args JSON (champ 'action'):
  {"action":"list"}                                    -> bases DB_CENTRALE + tables
  {"db":"destin_obs","sql":"SELECT ... LIMIT 5"}       -> requête par NOM (DB_CENTRALE)
  {"db_path":"/home/moussa/x/foo.db","sql":"..."}      -> requête par CHEMIN (guarded)
  {"action":"schema","db":"larousse_fts"}              -> CREATE TABLE de toutes les tables
  {"action":"schema","db":"larousse_fts","table":"t"}  -> CREATE TABLE d'une table
  {"action":"search","db":"larousse_fts","table":"defs","column":"txt","term":"chien"}
       -> full-text (FTS5 si <table>_fts existe, sinon LIKE sur column)

Sécurité : ouverture mode=ro (aucune écriture), whitelist SELECT/PRAGMA/WITH pour
'query', db par nom borné à DB_CENTRALE, db_path arbitraire filtré par guarded_path
(R16 : OS système / /mnt/archives / secrets home refusés). R6, R9.
"""
import sys
import json
import sqlite3
from pathlib import Path
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # agent/
from security.safe_path import guarded_path, PathBlockedError

_DB_ROOT = Path("/home/moussa/DB_CENTRALE")


def _dbs() -> dict:
    if not _DB_ROOT.exists():
        return {}
    return {p.stem: p for p in _DB_ROOT.rglob("*.db") if "backups" not in p.parts}


class Args(BaseModel):
    action: str = Field(default="query", description="query | list | schema | search")
    db: str | None = Field(default=None, description="Nom de base DB_CENTRALE (sans .db)")
    db_path: str | None = Field(default=None, description="Chemin absolu d'une base (alternative à db)")
    sql: str | None = Field(default=None, description="Requête SELECT/PRAGMA/WITH (action query)")
    table: str | None = Field(default=None, description="Table (schema/search)")
    column: str | None = Field(default=None, description="Colonne (search, fallback LIKE)")
    term: str | None = Field(default=None, description="Terme recherché (search)")
    limit: int = Field(default=50, description="Nb max de lignes")


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _err(msg: str):
    print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))


def _resolve_path(a: Args):
    """Retourne (path, label) ou None (après avoir imprimé l'erreur)."""
    if a.db_path:
        try:
            p = guarded_path(a.db_path)
        except PathBlockedError as e:
            _err(str(e)); return None
        if not p.exists():
            _err(f"Base introuvable: {p}"); return None
        return p, str(p)
    dbs = _dbs()
    if not dbs:
        _err(f"Aucune base dans {_DB_ROOT}"); return None
    if not a.db:
        _err("Préciser 'db' (nom DB_CENTRALE) ou 'db_path' (chemin)."); return None
    if a.db not in dbs:
        _err(f"Base inconnue: {a.db}. Dispo: {list(dbs)}"); return None
    return dbs[a.db], a.db


def run(json_args: str):
    try:
        a = Args.model_validate_json(json_args)

        # action list : bases DB_CENTRALE + tables
        if a.action == "list" or (a.action == "query" and not a.db and not a.db_path):
            dbs = _dbs()
            if not dbs:
                _err(f"Aucune base dans {_DB_ROOT}"); return
            out = {}
            for name, path in sorted(dbs.items()):
                try:
                    con = _connect(path)
                    out[name] = [r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    con.close()
                except sqlite3.Error as e:
                    out[name] = f"err: {e}"
            print(json.dumps({"status": "success", "databases": out}, ensure_ascii=False))
            return

        resolved = _resolve_path(a)
        if not resolved:
            return
        path, label = resolved

        if a.action == "schema":
            con = _connect(path)
            if a.table:
                row = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=? AND name NOT LIKE 'sqlite_%'",
                    (a.table,)).fetchone()
                con.close()
                print(json.dumps({"status": "success", "db": label, "table": a.table,
                                  "sql": row[0] if row else None}, ensure_ascii=False))
            else:
                rows = con.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                con.close()
                print(json.dumps({"status": "success", "db": label,
                                  "tables": [{"name": r[0], "sql": r[1]} for r in rows]},
                                 ensure_ascii=False))
            return

        if a.action == "search":
            if not a.table or not a.term:
                _err("search: 'table' et 'term' requis (et 'column' pour le fallback LIKE)."); return
            con = _connect(path)
            # Anti-injection par identifiant : valider table/colonne contre le
            # schéma réel AVANT toute interpolation (le mode ro empêche l'écriture
            # mais pas l'exfiltration via 'column = x FROM autre_table --').
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            if a.table not in tables:
                con.close(); _err(f"Table inconnue: {a.table}"); return
            if a.column:
                cols = {r[1] for r in con.execute(f'PRAGMA table_info("{a.table}")')}
                if a.column not in cols:
                    con.close(); _err(f"Colonne inconnue: {a.column}"); return
            fts = f"{a.table}_fts"
            has_fts = fts in tables
            try:
                if has_fts:
                    cur = con.execute(f'SELECT * FROM "{fts}" WHERE "{fts}" MATCH ? LIMIT ?',
                                      (a.term, a.limit))
                elif a.column:
                    cur = con.execute(f'SELECT * FROM "{a.table}" WHERE "{a.column}" LIKE ? LIMIT ?',
                                      (f"%{a.term}%", a.limit))
                else:
                    con.close()
                    _err("Pas de table FTS ; fournir 'column' pour un fallback LIKE."); return
                rows = [dict(r) for r in cur.fetchall()]
            finally:
                con.close()
            print(json.dumps({"status": "success", "db": label, "fts": bool(has_fts),
                              "count": len(rows), "rows": rows}, ensure_ascii=False, default=str))
            return

        # action query (défaut) : whitelist lecture seule
        if not a.sql or not a.sql.strip().lower().startswith(("select", "pragma", "with")):
            _err("Seules les requêtes SELECT/PRAGMA/WITH sont autorisées (lecture seule)."); return
        con = _connect(path)
        rows = [dict(r) for r in con.execute(a.sql).fetchmany(a.limit)]
        con.close()
        print(json.dumps({"status": "success", "db": label, "count": len(rows), "rows": rows},
                         ensure_ascii=False, default=str))
    except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
        _err(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")
