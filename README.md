# Dicionario de Libras — expansao (AMS)

Prototipo do trabalho final: CRUD de palavras, acepcoes, variacoes linguisticas, upload de video com consentimento e fluxo de aprovacao.

**Equipe:** Luis Fernando Fabricante, Gabriela Alves Ilezyszyn, Arthur Ari Lemke

## Como executar



```bash

pip install -r requirements.txt           # instala o flask

python app.py                             # abre em http://127.0.0.1:5000

python -m unittest discover -s tests -v   # pra rodar testes

```



Roteiro de demonstracao

1. Entre como **colaborador** → *Nova palavra* → cadastre acepcao e variacao PADRAO.
2. Em *Enviar video*, tente um menor de 18 anos sem responsavel (recusado, RN2); depois envie corretamente.
3. *Enviar para revisao*. Saia: o visitante nao va o verbete nem o vídeo (RN6).
4. Entre como **revisor** (outro nome) → *Fila de revisao* → aprove, peca ajustes ou reprove.
5. Como visitante, busque a palavra publicada. Como colaborador, edite-a: volta a rascunho (RN5).
6. Como **administrador**: log de auditoria, revogar consentimento e executar descarte.



## Limitacoes

Login sem senha, sem CSRF, sem e-mail, duração do vídeo não verificada, SQLite e pasta local no lugar de PostgreSQL/S3.



## Divisao de atividades

(preencher com o que cada integrante fez)

