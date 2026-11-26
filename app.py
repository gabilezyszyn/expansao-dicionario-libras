"""Dicionário de Libras expandido - protótipo (AMS0002). Flask + SQLite."""
import os, sqlite3, hashlib, uuid, datetime as dt
from functools import wraps
from flask import (Flask, g, request, redirect, render_template, flash,
                   session, abort, send_from_directory)

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("LIBRAS_DB", os.path.join(BASE, "dicionario.db"))
UP = os.path.join(BASE, "uploads")
MAX_MB = 50
DESCARTE_DIAS = 30
STATUS_ROTULO = {"RASCUNHO": ("✎", "Rascunho"), "EM_ANALISE": ("⏳", "Em análise"),
    "AJUSTES_SOLICITADOS": ("↩", "Ajustes solicitados"), "PUBLICADA": ("✔", "Publicada"), "REPROVADA": ("✖", "Reprovada")}
TIPOS = ["PADRAO", "REGIONAL", "SOCIAL", "HISTORICA", "GERACIONAL"]

app = Flask(__name__)
app.secret_key = "demo-trocar-em-producao"
app.config["MAX_CONTENT_LENGTH"] = (MAX_MB + 5) * 1024 * 1024

FIELDS = {  # (coluna, rótulo, obrigatório)
    "palavra": [("termo", "Termo", True), ("classe", "Classe gramatical", True),
                ("origem", "Origem", False), ("assunto", "Assunto", False)],
    "acepcao": [("definicao", "Definição", True), ("exemplo", "Exemplo em português", False),
                ("exemplo_libras", "Exemplo em Libras (glosa)", False)],
    "variacao": [("glosa", "Glosa (ex.: PAI2)", True), ("tipo", "Tipo", True),
                 ("uf", "UF (obrigatória se regional)", False), ("descricao", "Descrição", False),
                 ("config_mao", "Configuração de mão", False), ("signwriting", "SignWriting", False)],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS palavra(id INTEGER PRIMARY KEY, termo TEXT, classe TEXT, origem TEXT,
  assunto TEXT, status TEXT DEFAULT 'RASCUNHO', tipo_op TEXT DEFAULT 'INSERCAO', autor TEXT, enviado_em TEXT);
CREATE TABLE IF NOT EXISTS acepcao(id INTEGER PRIMARY KEY, palavra_id INTEGER REFERENCES palavra(id) ON DELETE CASCADE,
  definicao TEXT, exemplo TEXT, exemplo_libras TEXT);
CREATE TABLE IF NOT EXISTS variacao(id INTEGER PRIMARY KEY, palavra_id INTEGER REFERENCES palavra(id) ON DELETE CASCADE,
  glosa TEXT, tipo TEXT, uf TEXT, descricao TEXT, config_mao TEXT, signwriting TEXT);
CREATE TABLE IF NOT EXISTS video(id INTEGER PRIMARY KEY, variacao_id INTEGER REFERENCES variacao(id) ON DELETE CASCADE,
  arquivo TEXT, hash TEXT, sinalizante TEXT, nascimento TEXT, responsavel TEXT, termo TEXT,
  status TEXT DEFAULT 'QUARENTENA', descarte_em TEXT, criado_em TEXT);
CREATE TABLE IF NOT EXISTS revisao(id INTEGER PRIMARY KEY, palavra_id INTEGER REFERENCES palavra(id) ON DELETE CASCADE,
  revisor TEXT, decisao TEXT, comentario TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS auditoria(id INTEGER PRIMARY KEY, usuario TEXT, acao TEXT, detalhe TEXT, data TEXT);
"""

# ---------- utilidades ----------
def agora(): return dt.datetime.now().isoformat(timespec="seconds")

def conn():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def fechar(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, a=(), one=False):
    cur = conn().execute(sql, a)
    return cur.fetchone() if one else cur.fetchall()

def ex(sql, a=()):
    cur = conn().execute(sql, a); conn().commit(); return cur.lastrowid

def log(acao, detalhe=""):
    u = session.get("u")
    ex("INSERT INTO auditoria(usuario,acao,detalhe,data) VALUES(?,?,?,?)",
       (u["nome"] if u else "anonimo", acao, detalhe, agora()))

def init_db():
    with sqlite3.connect(DB) as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM palavra").fetchone():
            seed = [("MAMÃE", "SUBSTANTIVO", "nacional", "FAMÍLIA", "A mulher que tem filhos.",
                     [("MAMÃE1", "PADRAO", "")]),
                    ("PAI", "SUBSTANTIVO", "nacional", "FAMÍLIA", "O homem que tem filhos.",
                     [("PAI1", "PADRAO", ""), ("PAI2", "REGIONAL", "RJ"), ("PAI3", "REGIONAL", "RS")])]
            for t, cl, o, a, d, vs in seed:
                pid = c.execute("INSERT INTO palavra(termo,classe,origem,assunto,status,autor) VALUES(?,?,?,?,'PUBLICADA','sistema')",
                                (t, cl, o, a)).lastrowid
                c.execute("INSERT INTO acepcao(palavra_id,definicao) VALUES(?,?)", (pid, d))
                for g_, tp, uf in vs:
                    c.execute("INSERT INTO variacao(palavra_id,glosa,tipo,uf) VALUES(?,?,?,?)", (pid, g_, tp, uf))

def need(*perfis):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = session.get("u")
            if not u: return redirect("/login")
            if u["perfil"] not in perfis: abort(403)
            return f(*a, **k)
        return w
    return deco

@app.context_processor
def ctx(): return {"user": session.get("u"), "TIPOS": TIPOS, "ST": STATUS_ROTULO}

def rm_files(rows):
    for r in rows:
        for campo in ("arquivo", "termo"):
            try:
                if r[campo]: os.remove(os.path.join(UP, r[campo]))
            except (OSError, KeyError): pass

def videos_da_palavra(pid):
    return q("SELECT v.* FROM video v JOIN variacao s ON s.id=v.variacao_id WHERE s.palavra_id=?", (pid,))

def pode_editar(p):
    u = session.get("u")
    return bool(u) and u["perfil"] in ("colaborador", "administrador") and p["status"] != "EM_ANALISE"

def tocar(pid):
    """RN5: alterar conteúdo publicado exige nova revisão."""
    p = q("SELECT * FROM palavra WHERE id=?", (pid,), one=True)
    if p["status"] == "PUBLICADA":
        ex("UPDATE palavra SET status='RASCUNHO', tipo_op='ALTERACAO' WHERE id=?", (pid,))
        flash("Verbete publicado alterado: ele volta a rascunho e precisa de nova revisão (RN5).")

def validar(t, f, pid, rid=None):
    erros = [f"{l} é obrigatório." for c, l, o in FIELDS[t] if o and not f.get(c, "").strip()]
    if t == "variacao":
        if f.get("tipo") == "REGIONAL" and not f.get("uf", "").strip():
            erros.append("Variação regional exige a UF (RN4).")
        if f.get("tipo") == "PADRAO" and q("SELECT 1 FROM variacao WHERE palavra_id=? AND tipo='PADRAO' AND id IS NOT ?", (pid, rid), one=True):
            erros.append("Este verbete já tem uma variação padrão (RN4).")
    return erros

# ---------- autenticação (demo) ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome, perfil = request.form.get("nome", "").strip(), request.form.get("perfil")
        if not nome or perfil not in ("colaborador", "revisor", "administrador"):
            flash("Informe seu nome e escolha um perfil."); return redirect("/login")
        session["u"] = {"nome": nome, "perfil": perfil}; log("LOGIN", perfil)
        return redirect("/")
    return render_template("login.html")

@app.route("/sair", methods=["POST"])
def sair(): session.clear(); return redirect("/")

# ---------- consulta ----------
@app.route("/")
def index():
    termo, por = request.args.get("q", "").strip(), request.args.get("por", "palavra")
    ordem = request.args.get("ordem", "alfabetica")
    sql = ("SELECT DISTINCT p.* FROM palavra p LEFT JOIN acepcao a ON a.palavra_id=p.id WHERE 1=1")
    args = []
    if not session.get("u"): sql += " AND p.status='PUBLICADA'"
    if termo:
        col = {"palavra": "p.termo", "acepcao": "a.definicao", "assunto": "p.assunto"}.get(por)
        if por == "exemplo": sql += " AND (a.exemplo LIKE ? OR a.exemplo_libras LIKE ?)"; args += [f"%{termo}%"] * 2
        elif col: sql += f" AND {col} LIKE ?"; args.append(f"%{termo}%")
    sql += " ORDER BY " + ("p.assunto, p.termo" if ordem == "assunto" else "p.termo")
    return render_template("index.html", rows=q(sql, args), termo=termo, por=por, ordem=ordem)

@app.route("/palavra/<int:pid>")
def ver(pid):
    p = q("SELECT * FROM palavra WHERE id=?", (pid,), one=True) or abort(404)
    u = session.get("u")
    if p["status"] != "PUBLICADA" and not u: abort(404)
    vs = []
    for v in q("SELECT * FROM variacao WHERE palavra_id=? ORDER BY tipo<>'PADRAO', id", (pid,)):
        v = dict(v)
        v["videos"] = q("SELECT * FROM video WHERE variacao_id=? AND status<>'DESCARTADO'", (v["id"],)) if u else \
                      q("SELECT * FROM video WHERE variacao_id=? AND status='DISPONIVEL'", (v["id"],))
        vs.append(v)
    return render_template("palavra.html", p=p, edit=pode_editar(p), variacoes=vs,
                           acepcoes=q("SELECT * FROM acepcao WHERE palavra_id=?", (pid,)),
                           revisoes=q("SELECT * FROM revisao WHERE palavra_id=? ORDER BY id DESC", (pid,)))

@app.route("/midia/<int:vid>")
def midia(vid):
    v = q("SELECT v.*, p.status AS pst FROM video v JOIN variacao s ON s.id=v.variacao_id JOIN palavra p ON p.id=s.palavra_id WHERE v.id=?", (vid,), one=True) or abort(404)
    publico = v["status"] == "DISPONIVEL" and v["pst"] == "PUBLICADA"
    if not publico and not session.get("u"): abort(404)   # RN6: quarentena
    return send_from_directory(UP, v["arquivo"])

@app.route("/termo/<int:vid>")
@need("revisor", "administrador")
def termo(vid):
    v = q("SELECT * FROM video WHERE id=?", (vid,), one=True) or abort(404)
    log("ACESSO_TERMO", f"video {vid}")
    return send_from_directory(UP, v["termo"])

# ---------- CRUD genérico ----------
def form_view(t, rid=None, pid=None):
    atual = q(f"SELECT * FROM {t} WHERE id=?", (rid,), one=True) if rid else None
    if rid and not atual: abort(404)
    if t != "palavra" and rid: pid = atual["palavra_id"]
    if t == "palavra" and rid: pid = rid
    if pid:
        p = q("SELECT * FROM palavra WHERE id=?", (pid,), one=True) or abort(404)
        if not pode_editar(p): flash("Este verbete está em análise e não pode ser editado."); return redirect(f"/palavra/{pid}")
    if request.method == "POST":
        f = request.form
        erros = validar(t, f, pid, rid)
        if erros:
            for e in erros: flash(e)
            return render_template("form.html", t=t, campos=FIELDS[t], v=f, rid=rid)
        cols = [c for c, _, _ in FIELDS[t]]; vals = [f.get(c, "").strip() for c in cols]
        if rid:
            ex(f"UPDATE {t} SET " + ",".join(c + "=?" for c in cols) + " WHERE id=?", vals + [rid]); log("ALTERAR", f"{t} {rid}")
        elif t == "palavra":
            pid = ex("INSERT INTO palavra(termo,classe,origem,assunto,autor) VALUES(?,?,?,?,?)", vals + [session["u"]["nome"]]); log("CRIAR", f"palavra {pid}")
        else:
            ex(f"INSERT INTO {t}(palavra_id," + ",".join(cols) + ") VALUES(?," + ",".join("?" * len(cols)) + ")", [pid] + vals); log("CRIAR", t)
        if rid or t != "palavra": tocar(pid)
        flash("Salvo."); return redirect(f"/palavra/{pid}")
    return render_template("form.html", t=t, campos=FIELDS[t], v=atual or {}, rid=rid)

@app.route("/palavra/nova", methods=["GET", "POST"])
@need("colaborador", "administrador")
def nova_palavra(): return form_view("palavra")

@app.route("/palavra/<int:pid>/<t>/nova", methods=["GET", "POST"])
@need("colaborador", "administrador")
def novo_filho(pid, t):
    if t not in ("acepcao", "variacao"): abort(404)
    return form_view(t, pid=pid)

@app.route("/<t>/<int:rid>/editar", methods=["GET", "POST"])
@need("colaborador", "administrador")
def editar(t, rid):
    if t not in FIELDS: abort(404)
    return form_view(t, rid=rid)

@app.route("/<t>/<int:rid>/excluir", methods=["POST"])
@need("colaborador", "administrador")
def excluir(t, rid):
    if t not in FIELDS: abort(404)
    r = q(f"SELECT * FROM {t} WHERE id=?", (rid,), one=True) or abort(404)
    pid = rid if t == "palavra" else r["palavra_id"]
    p = q("SELECT * FROM palavra WHERE id=?", (pid,), one=True)
    if not pode_editar(p): flash("Verbete em análise: não pode ser alterado."); return redirect(f"/palavra/{pid}")
    admin = session["u"]["perfil"] == "administrador"
    if t == "palavra":
        if p["status"] == "PUBLICADA" and not admin:   # RN5: solicitação de exclusão
            ex("UPDATE palavra SET status='EM_ANALISE', tipo_op='EXCLUSAO', enviado_em=? WHERE id=?", (agora(), pid))
            log("SOLICITAR_EXCLUSAO", f"palavra {pid}"); flash("Solicitação de exclusão enviada para revisão."); return redirect(f"/palavra/{pid}")
        rm_files(videos_da_palavra(pid)); ex("DELETE FROM palavra WHERE id=?", (pid,)); log("EXCLUIR", f"palavra {pid}")
        flash("Palavra excluída."); return redirect("/")
    if t == "variacao": rm_files(q("SELECT * FROM video WHERE variacao_id=?", (rid,)))
    ex(f"DELETE FROM {t} WHERE id=?", (rid,)); log("EXCLUIR", f"{t} {rid}"); tocar(pid)
    flash("Excluído."); return redirect(f"/palavra/{pid}")

# ---------- vídeo ----------
def assinatura_ok(b):
    return b[4:8] == b"ftyp" or b[:4] == b"\x1a\x45\xdf\xa3"   # MP4 / WebM

@app.route("/variacao/<int:vid>/video", methods=["GET", "POST"])
@need("colaborador", "administrador")
def enviar_video(vid):
    s = q("SELECT * FROM variacao WHERE id=?", (vid,), one=True) or abort(404)
    p = q("SELECT * FROM palavra WHERE id=?", (s["palavra_id"],), one=True)
    if not pode_editar(p): flash("Verbete em análise."); return redirect(f"/palavra/{p['id']}")
    if request.method == "POST":
        f, arq, trm = request.form, request.files.get("arquivo"), request.files.get("termo")
        erros = []
        nome, nasc = f.get("sinalizante", "").strip(), f.get("nascimento", "")
        if not nome or not nasc: erros.append("Identifique o sinalizante com nome e data de nascimento (RN1).")
        if not trm or not trm.filename: erros.append("Anexe o termo de autorização de imagem (RN1).")
        if not arq or not arq.filename: erros.append("Selecione o vídeo.")
        menor = False
        try:
            n = dt.date.fromisoformat(nasc); h = dt.date.today()
            menor = (h.year - n.year - ((h.month, h.day) < (n.month, n.day))) < 18
        except ValueError:
            if nasc: erros.append("Data de nascimento inválida.")
        if menor and not f.get("responsavel", "").strip():
            erros.append("Sinalizante menor de 18 anos: informe o responsável legal, que deve assinar o termo (RN2).")
        dados = arq.read() if arq and arq.filename else b""
        if arq and arq.filename:
            if not arq.filename.lower().endswith((".mp4", ".webm")) or not assinatura_ok(dados[:12]):
                erros.append("Formato inválido: envie MP4 ou WebM (RN7).")
            if len(dados) > MAX_MB * 1024 * 1024: erros.append(f"Arquivo acima de {MAX_MB} MB (RN7).")
        if erros:
            for e in erros: flash(e)
            return render_template("video.html", s=s, p=p, v=f)
        ext = os.path.splitext(arq.filename)[1].lower()
        na, nt = uuid.uuid4().hex + ext, uuid.uuid4().hex + os.path.splitext(trm.filename)[1].lower()
        open(os.path.join(UP, na), "wb").write(dados); trm.save(os.path.join(UP, nt))
        ex("INSERT INTO video(variacao_id,arquivo,hash,sinalizante,nascimento,responsavel,termo,criado_em) VALUES(?,?,?,?,?,?,?,?)",
           (vid, na, hashlib.sha256(dados).hexdigest(), nome, nasc, f.get("responsavel", "").strip(), nt, agora()))
        log("UPLOAD_VIDEO", f"variacao {vid}"); tocar(p["id"])
        flash("Vídeo enviado e mantido em quarentena até a aprovação."); return redirect(f"/palavra/{p['id']}")
    return render_template("video.html", s=s, p=p, v={})

@app.route("/video/<int:vid>/excluir", methods=["POST"])
@need("colaborador", "administrador")
def excluir_video(vid):
    v = q("SELECT v.*, s.palavra_id AS pid FROM video v JOIN variacao s ON s.id=v.variacao_id WHERE v.id=?", (vid,), one=True) or abort(404)
    rm_files([v]); ex("DELETE FROM video WHERE id=?", (vid,)); log("EXCLUIR", f"video {vid}"); tocar(v["pid"])
    flash("Vídeo excluído."); return redirect(f"/palavra/{v['pid']}")

@app.route("/video/<int:vid>/revogar", methods=["POST"])
@need("administrador")
def revogar(vid):
    """RF20: consentimento revogado -> despublica já e agenda descarte."""
    v = q("SELECT v.*, s.palavra_id AS pid FROM video v JOIN variacao s ON s.id=v.variacao_id WHERE v.id=?", (vid,), one=True) or abort(404)
    ex("UPDATE video SET status='REVOGADO', descarte_em=? WHERE id=?",
       ((dt.datetime.now() + dt.timedelta(days=DESCARTE_DIAS)).isoformat(timespec="seconds"), vid))
    log("REVOGAR_CONSENTIMENTO", f"video {vid}"); flash("Consentimento revogado: vídeo despublicado.")
    return redirect(f"/palavra/{v['pid']}")

# ---------- fluxo de aprovação ----------
@app.route("/palavra/<int:pid>/enviar", methods=["POST"])
@need("colaborador", "administrador")
def enviar(pid):
    p = q("SELECT * FROM palavra WHERE id=?", (pid,), one=True) or abort(404)
    if p["status"] not in ("RASCUNHO", "AJUSTES_SOLICITADOS"): abort(400)
    erros = []
    if not q("SELECT 1 FROM acepcao WHERE palavra_id=?", (pid,), one=True): erros.append("Cadastre ao menos uma acepção.")
    if q("SELECT COUNT(*) c FROM variacao WHERE palavra_id=? AND tipo='PADRAO'", (pid,), one=True)["c"] != 1:
        erros.append("O verbete precisa de exatamente uma variação padrão (RN4).")
    if erros:
        for e in erros: flash(e)
        return redirect(f"/palavra/{pid}")
    ex("UPDATE palavra SET status='EM_ANALISE', enviado_em=? WHERE id=?", (agora(), pid)); log("ENVIAR_REVISAO", f"palavra {pid}")
    flash("Enviado para revisão."); return redirect(f"/palavra/{pid}")

@app.route("/fila")
@need("revisor", "administrador")
def fila():
    return render_template("fila.html", rows=q("SELECT * FROM palavra WHERE status='EM_ANALISE' ORDER BY enviado_em"))

@app.route("/palavra/<int:pid>/revisar", methods=["POST"])
@need("revisor")
def revisar(pid):
    p = q("SELECT * FROM palavra WHERE id=?", (pid,), one=True) or abort(404)
    u, dec, com = session["u"], request.form.get("decisao"), request.form.get("comentario", "").strip()
    if p["status"] != "EM_ANALISE": abort(400)
    if p["autor"] == u["nome"]: flash("Você não pode revisar a própria submissão (RN3)."); return redirect(f"/palavra/{pid}")
    if dec not in ("APROVAR", "AJUSTES", "REPROVAR"): abort(400)
    if dec != "APROVAR" and not com: flash("A justificativa é obrigatória para ajustes e reprovação."); return redirect(f"/palavra/{pid}")
    ex("INSERT INTO revisao(palavra_id,revisor,decisao,comentario,data) VALUES(?,?,?,?,?)", (pid, u["nome"], dec, com, agora()))
    vids = videos_da_palavra(pid)
    if dec == "APROVAR":
        if p["tipo_op"] == "EXCLUSAO":
            rm_files(vids); ex("DELETE FROM palavra WHERE id=?", (pid,)); log("EXCLUSAO_APROVADA", f"palavra {pid}")
            flash("Exclusão aprovada."); return redirect("/fila")
        ex("UPDATE palavra SET status='PUBLICADA', tipo_op='INSERCAO' WHERE id=?", (pid,))
        ex("UPDATE video SET status='DISPONIVEL' WHERE status='QUARENTENA' AND variacao_id IN (SELECT id FROM variacao WHERE palavra_id=?)", (pid,))
    elif dec == "AJUSTES":
        ex("UPDATE palavra SET status='AJUSTES_SOLICITADOS' WHERE id=?", (pid,))
    else:
        ex("UPDATE palavra SET status='REPROVADA' WHERE id=?", (pid,))
        ex("UPDATE video SET status='REPROVADO', descarte_em=? WHERE variacao_id IN (SELECT id FROM variacao WHERE palavra_id=?)",
           ((dt.datetime.now() + dt.timedelta(days=DESCARTE_DIAS)).isoformat(timespec="seconds"), pid))
    log("REVISAO_" + dec, f"palavra {pid}"); flash("Decisão registrada.")
    return redirect("/fila")

# ---------- administração / LGPD ----------
@app.route("/admin")
@need("administrador")
def admin():
    return render_template("admin.html", logs=q("SELECT * FROM auditoria ORDER BY id DESC LIMIT 100"),
                           pend=q("SELECT * FROM video WHERE descarte_em IS NOT NULL AND status<>'DESCARTADO'"))

@app.route("/admin/descarte", methods=["POST"])
@need("administrador")
def descarte():
    venc = q("SELECT * FROM video WHERE descarte_em IS NOT NULL AND descarte_em<=? AND status<>'DESCARTADO'", (agora(),))
    rm_files(venc)
    for v in venc: ex("UPDATE video SET status='DESCARTADO', arquivo=NULL, termo=NULL WHERE id=?", (v["id"],))
    log("DESCARTE", f"{len(venc)} vídeo(s)"); flash(f"{len(venc)} vídeo(s) descartado(s).")
    return redirect("/admin")

init_db()
if __name__ == "__main__":
    app.run(debug=True)
