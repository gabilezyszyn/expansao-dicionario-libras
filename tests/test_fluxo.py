import os, sys, io, tempfile, unittest
os.environ["LIBRAS_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64

class Fluxo(unittest.TestCase):
    def cli(self, nome, perfil):
        c = A.app.test_client(); c.post("/login", data={"nome": nome, "perfil": perfil}); return c

    def n(self, sql, a=()):
        with A.app.app_context(): return A.q(sql, a, one=True)[0]

    def test_fluxo_completo(self):
        col, rev, vis = self.cli("Carlos", "colaborador"), self.cli("Renata", "revisor"), A.app.test_client()
        col.post("/palavra/nova", data={"termo": "AVÓ", "classe": "SUBSTANTIVO", "origem": "nacional", "assunto": "FAMÍLIA"})
        pid = self.n("SELECT id FROM palavra WHERE termo='AVÓ'")
        col.post(f"/palavra/{pid}/acepcao/nova", data={"definicao": "Mãe do pai ou da mãe."})
        col.post(f"/palavra/{pid}/variacao/nova", data={"glosa": "AVÓ1", "tipo": "PADRAO"})
        col.post(f"/palavra/{pid}/variacao/nova", data={"glosa": "AVÓ2", "tipo": "REGIONAL", "uf": ""})
        self.assertEqual(self.n("SELECT COUNT(*) FROM variacao WHERE palavra_id=?", (pid,)), 1)  # RN4
        vid = self.n("SELECT id FROM variacao WHERE palavra_id=?", (pid,))
        def up(nasc, resp="", termo=True):
            d = {"arquivo": (io.BytesIO(MP4), "a.mp4"), "sinalizante": "Ana", "nascimento": nasc, "responsavel": resp}
            if termo: d["termo"] = (io.BytesIO(b"pdf"), "t.pdf")
            return col.post(f"/variacao/{vid}/video", data=d, content_type="multipart/form-data")
        up("2015-01-01"); up("1990-01-01", termo=False)   # RN2 e RN1
        self.assertEqual(self.n("SELECT COUNT(*) FROM video"), 0)
        up("1990-01-01")
        self.assertEqual(self.n("SELECT COUNT(*) FROM video"), 1)
        col.post(f"/palavra/{pid}/enviar")
        self.assertEqual(vis.get(f"/palavra/{pid}").status_code, 404)  # RN6
        v = self.n("SELECT id FROM video")
        self.assertEqual(vis.get(f"/midia/{v}").status_code, 404)
        self.cli("Carlos", "revisor").post(f"/palavra/{pid}/revisar", data={"decisao": "APROVAR"})
        self.assertEqual(self.n("SELECT status FROM palavra WHERE id=?", (pid,)), "EM_ANALISE")  # RN3
        rev.post(f"/palavra/{pid}/revisar", data={"decisao": "AJUSTES", "comentario": "filmar de novo"})
        col.post(f"/palavra/{pid}/enviar"); rev.post(f"/palavra/{pid}/revisar", data={"decisao": "APROVAR"})
        self.assertEqual(vis.get(f"/palavra/{pid}").status_code, 200)
        self.assertEqual(vis.get(f"/midia/{v}").status_code, 200)
        col.post(f"/palavra/{pid}/acepcao/nova", data={"definicao": "outra"})   # RN5
        self.assertEqual(self.n("SELECT status FROM palavra WHERE id=?", (pid,)), "RASCUNHO")

    def test_reprovacao_descarte_e_perfis(self):
        col, rev, adm = self.cli("Carlos", "colaborador"), self.cli("Renata", "revisor"), self.cli("Admin", "administrador")
        self.assertEqual(col.get("/admin").status_code, 403)
        self.assertEqual(A.app.test_client().get("/palavra/nova").status_code, 302)
        col.post("/palavra/nova", data={"termo": "TIO", "classe": "SUBSTANTIVO"})
        pid = self.n("SELECT id FROM palavra WHERE termo='TIO'")
        col.post(f"/palavra/{pid}/acepcao/nova", data={"definicao": "x"})
        col.post(f"/palavra/{pid}/variacao/nova", data={"glosa": "TIO1", "tipo": "PADRAO"})
        vid = self.n("SELECT id FROM variacao WHERE palavra_id=?", (pid,))
        col.post(f"/variacao/{vid}/video", data={"arquivo": (io.BytesIO(MP4), "a.mp4"), "sinalizante": "Bia",
                 "nascimento": "1990-01-01", "termo": (io.BytesIO(b"p"), "t.pdf")}, content_type="multipart/form-data")
        col.post(f"/palavra/{pid}/enviar"); rev.post(f"/palavra/{pid}/revisar", data={"decisao": "REPROVAR", "comentario": "ruim"})
        self.assertEqual(self.n("SELECT status FROM video WHERE variacao_id=?", (vid,)), "REPROVADO")
        with A.app.app_context(): A.ex("UPDATE video SET descarte_em='2000-01-01T00:00:00' WHERE variacao_id=?", (vid,))
        adm.post("/admin/descarte")
        self.assertEqual(self.n("SELECT status FROM video WHERE variacao_id=?", (vid,)), "DESCARTADO")
        self.assertGreater(self.n("SELECT COUNT(*) FROM auditoria"), 5)

if __name__ == "__main__": unittest.main()
