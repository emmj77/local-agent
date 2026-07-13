"""styles.py — CSS sombre + JS auto-refresh pour le dashboard.

Constantes statiques, ne changent jamais. Importé une fois au démarrage.
"""

CSS = """
<style>
:root{
  --bg:#0d1117; --bg2:#161b22; --border:#30363d;
  --text:#c9d1d9; --muted:#8b949e; --blue:#58a6ff;
  --green:#3fb950; --red:#f85149; --yellow:#d29922;
}
.stApp{background:var(--bg);color:var(--text)}
[data-testid="stHeader"]{background:transparent}
h1,h2,h3{color:var(--blue)!important;letter-spacing:.2px}
.stApp h2{font-size:17px;border-bottom:1px solid var(--border);padding-bottom:6px}
[data-testid="stCaptionContainer"]{color:var(--muted)}

section[data-testid="stSidebar"]{background:var(--bg2);border-right:1px solid var(--border);max-width:11rem}
section[data-testid="stSidebar"] h1{font-size:18px}

/* Colonne gauche / droite : filet de séparation */
[data-testid="stColumn"]{padding:0 6px}

/* Onglets de chat (façon HERMES_LITE) */
.stTabs [data-baseweb="tab-list"]{gap:2px;flex-wrap:wrap}
.stTabs [data-baseweb="tab"]{background:var(--bg2);border:1px solid var(--border);
  border-radius:6px 6px 0 0;padding:3px 12px;color:var(--muted);font-size:13px}
.stTabs [aria-selected="true"]{color:var(--blue);border-bottom:2px solid var(--blue)}

/* Cartes de chat */
[data-testid="stChatMessage"]{background:var(--bg2);border:1px solid var(--border);
  border-radius:10px;padding:8px 12px;margin-bottom:6px}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){
  background:rgba(88,166,255,.12);border-color:rgba(88,166,255,.35);margin-left:10%}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"]{color:var(--blue)}

[data-testid="stChatInput"]{background:var(--bg2);border:1px solid var(--border);border-radius:8px}
[data-testid="stChatInput"] textarea{color:var(--text)}

[data-testid="stExpander"]{border:1px solid var(--border);border-radius:8px;background:var(--bg)}
[data-testid="stExpander"] summary{color:var(--blue)}
[data-testid="stCode"],pre{background:#010409!important;border:1px solid var(--border);border-radius:6px}
code{color:var(--yellow)}

::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}

/* Cartes de page (gauche) */
.la-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:8px 12px;margin-bottom:8px}
.la-card b{color:var(--text)} .la-card small{color:var(--muted)}
.la-kv{display:flex;justify-content:space-between;padding:3px 0;font-size:13px;
  border-bottom:1px solid rgba(48,54,61,.5)}
.la-kv .k{color:var(--muted)} .la-kv .v{color:var(--text);font-weight:500}
.la-dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
.la-dot.up{background:var(--green)} .la-dot.down{background:var(--red)}
.la-badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;background:var(--bg2);
  border:1px solid var(--border);border-radius:20px;padding:2px 10px;margin:0 6px 6px 0;color:var(--muted)}
.la-badge b{color:var(--blue)}
/* Bulle outil compacte : nom + path/argument, faible hauteur, pas de dépliable */
.la-tool{background:#161b22;border-left:3px solid var(--blue);padding:2px 8px;margin:3px 0;
  border-radius:4px;font-size:0.82rem;line-height:1.35;color:#c9d1d9;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.la-tool code{color:#7ee787;background:transparent;font-size:0.8rem}
/* Bulle réflexion : grise, discrète */
.la-think{background:#1a1a2e;border-left:3px solid #6e7681;padding:3px 10px;margin:3px 0;
  border-radius:4px;font-size:0.8rem;color:#8b949e;font-style:italic}
/* Bulle résultat : verte, données brutes */
.la-result{background:#0d1b11;border-left:3px solid var(--green);padding:3px 10px;margin:3px 0;
  border-radius:4px;font-size:0.78rem;color:#7ee787;white-space:pre-wrap;word-break:break-word;
  max-height:120px;overflow-y:auto}
/* Bulles messages */
.la-msg{border:1px solid var(--border);border-radius:10px;padding:8px 12px;margin:6px 0;
  font-size:0.9rem;line-height:1.45;word-break:break-word}
.la-msg p{margin:0 0 2px 0}
.la-msg p:last-child{margin:0}
.la-msg ul,.la-msg ol{margin:2px 0;padding-left:20px}
.la-msg li{margin:0}
.la-msg pre{background:#0d1117;border:1px solid var(--border);border-radius:4px;
  padding:6px 10px;margin:4px 0;overflow-x:auto;font-size:0.82rem}
.la-msg code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:0.85rem}
.la-msg pre code{background:transparent;padding:0}
.la-msg code:not(pre code){background:rgba(110,118,129,.2);padding:1px 4px;border-radius:3px}
.la-msg h1,.la-msg h2,.la-msg h3{margin:4px 0 2px 0;font-size:1rem}
.la-msg table{border-collapse:collapse;margin:4px 0;font-size:0.82rem}
.la-msg th,.la-msg td{border:1px solid var(--border);padding:3px 8px}
.la-msg.user{background:rgba(88,166,255,.12);border-color:rgba(88,166,255,.35);
  margin-left:10%;color:var(--blue)}
.la-msg.assistant{background:var(--bg2);color:var(--text)}
.la-msg.think{background:rgba(137,87,229,.08);border-color:rgba(137,87,229,.25);
  border-style:dashed;color:#b392f0;font-size:0.85rem;opacity:0.85}
.la-tool-result{font-size:0.75rem;color:var(--muted);padding:1px 8px;margin:1px 0;
  font-family:ui-monospace,Menlo,Consolas,monospace}
/* Terminal unifié des agents (page Logs) */
.la-term{background:#0a0e14;border:1px solid var(--border);border-radius:6px;padding:10px 12px;
  font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:1.5;color:#c9d1d9;
  white-space:pre-wrap;word-break:break-word;height:660px;overflow-y:auto}

/* Page compacte (moins de scroll global) */
.block-container{padding-top:2.2rem;padding-bottom:0.5rem}
/* Supprimer le défilement de la page (les conteneurs internes scrollent eux-mêmes) */
section[data-testid="stMain"]{overflow:hidden}
/* Nav sidebar = boutons rectangulaires, bleu quand sélectionné (comme les onglets) */
section[data-testid="stSidebar"] .stButton>button{
  border-radius:6px;border:1px solid var(--border);text-align:left;
  background:var(--bg2);color:var(--text);margin-bottom:3px;font-size:14px}
section[data-testid="stSidebar"] .stButton>button:hover{border-color:var(--blue);color:var(--blue)}
section[data-testid="stSidebar"] .stButton>button[kind="primary"]{
  background:rgba(88,166,255,.15);border-color:var(--blue);color:var(--blue);font-weight:600}
</style>
"""

AUTO_REFRESH_JS = """
<script>
(function(){
  let timer=null,seconds=0;
  function update(){if(window._ar_display)window._ar_display.innerText=seconds>0?seconds+'s':''}
  window._ar_start=function(sec){
    if(timer){clearInterval(timer);timer=null}
    seconds=sec;update()
    if(sec>0){timer=setInterval(function(){seconds--;update();if(seconds<=0){clearInterval(timer);timer=null;window.location.reload()}},1000)}
  }
})();
</script>
"""