"""
Avaliação do SmartVitta - passo 1: extração.

Lê as fotos de notas de uma pasta, chama a API do Claude com o mesmo modelo,
prompt e schema usados no cenário do Make e salva o que o modelo devolveu.

Uso (no terminal, dentro da pasta do repositório):
    python eval/extrair.py PASTA_DAS_NOTAS PASTA_DE_SAIDA

A chave da API é lida da variável de ambiente ANTHROPIC_API_KEY.
Não usa bibliotecas externas.

Saídas, dentro de PASTA_DE_SAIDA:
    json/<nome da foto>.json   resposta do modelo para cada nota
    conferencia_itens.csv      uma linha por item extraído, para conferir à mão
    conferencia_notas.csv      uma linha por nota, para conferir à mão
"""

import base64
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODELO = "claude-haiku-5-5"
MAX_TOKENS = 8000
URL = "https://api.anthropic.com/v1/messages"
TIPOS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
LIMITE_BYTES = 3_700_000  # a API aceita até 5 MB por imagem depois de codificada em base64

RAIZ = Path(__file__).resolve().parent.parent
PROMPT = (RAIZ / "prompts" / "prompt_nfce.txt").read_text(encoding="utf-8").strip()
SCHEMA = json.loads((RAIZ / "prompts" / "schema_nfce.json").read_text(encoding="utf-8"))


def chamar_claude(foto: Path, chave: str) -> dict:
    """Mesma chamada do módulo HTTP do cenário; a imagem vai em base64 em vez de URL."""
    corpo = {
        "model": MODELO,
        "max_tokens": MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": TIPOS[foto.suffix.lower()],
                    "data": base64.b64encode(foto.read_bytes()).decode("ascii"),
                }},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }
    pedido = urllib.request.Request(
        URL,
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            "x-api-key": chave,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(pedido, timeout=120) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def br(numero) -> str:
    """Número no padrão brasileiro, para abrir certo no Excel/Sheets."""
    return str(numero).replace(".", ",")


def escrever_csv(caminho: Path, cabecalho: list, linhas: list) -> None:
    with open(caminho, "w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(cabecalho)
        escritor.writerows(linhas)


def gerar_conferencia(pasta_json: Path, pasta_saida: Path) -> None:
    itens, notas = [], []
    for arquivo in sorted(pasta_json.glob("*.json")):
        nota = json.loads(arquivo.read_text(encoding="utf-8"))["extracao"]
        soma = round(sum(i["valor_total"] for i in nota["itens"]), 2)
        notas.append([arquivo.stem, nota["status"], nota["estabelecimento"], len(nota["itens"]),
                      br(soma), br(nota["total_nota"]), "", "", ""])
        for n, i in enumerate(nota["itens"], start=1):
            # O apóstrofo força o Excel a tratar o código como texto (mantém zeros à esquerda).
            itens.append([arquivo.stem, n, i["item"], "'" + i["codigo"], br(i["quantidade"]),
                          br(i["valor_total"]), i["categoria"], "", "", "", "", ""])
    escrever_csv(pasta_saida / "conferencia_itens.csv",
                 ["nota", "n", "item", "codigo", "quantidade", "valor_total", "categoria",
                  "item_existe", "codigo_ok", "quantidade_ok", "valor_total_ok", "categoria_ok"],
                 itens)
    escrever_csv(pasta_saida / "conferencia_notas.csv",
                 ["nota", "status", "estabelecimento", "itens_extraidos", "soma_itens", "total_nota",
                  "itens_na_nota_real", "total_nota_ok", "observacao"],
                 notas)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Uso: python eval/extrair.py PASTA_DAS_NOTAS PASTA_DE_SAIDA")
    chave = os.environ.get("ANTHROPIC_API_KEY")
    if not chave:
        sys.exit("Defina a variável de ambiente ANTHROPIC_API_KEY antes de rodar.")

    pasta_notas, pasta_saida = Path(sys.argv[1]), Path(sys.argv[2])
    pasta_json = pasta_saida / "json"
    pasta_json.mkdir(parents=True, exist_ok=True)

    fotos = sorted(f for f in pasta_notas.iterdir() if f.suffix.lower() in TIPOS)
    if not fotos:
        sys.exit(f"Nenhuma foto (.jpg, .jpeg, .png, .webp) em {pasta_notas}")

    entrada = saida = 0
    for foto in fotos:
        destino = pasta_json / f"{foto.stem}.json"
        if destino.exists():
            print(f"{foto.name}: já extraída, pulando (apague {destino.name} para refazer)")
            continue
        if foto.stat().st_size > LIMITE_BYTES:
            print(f"{foto.name}: arquivo grande demais para a API, pulando (reduza a foto)")
            continue
        try:
            resposta = chamar_claude(foto, chave)
        except urllib.error.HTTPError as erro:
            print(f"{foto.name}: erro HTTP {erro.code} - {erro.read().decode('utf-8')[:300]}")
            continue
        except Exception as erro:  # rede, timeout
            print(f"{foto.name}: falha na chamada - {erro}")
            continue

        uso = resposta.get("usage", {})
        entrada += uso.get("input_tokens", 0)
        saida += uso.get("output_tokens", 0)
        if resposta.get("stop_reason") != "end_turn":
            print(f"{foto.name}: resposta incompleta (stop_reason={resposta.get('stop_reason')}), não salva")
            continue

        # A resposta pode trazer outros blocos antes do texto; pega o primeiro bloco de texto.
        texto = next((b["text"] for b in resposta["content"] if b.get("type") == "text"), None)
        if texto is None:
            tipos = [b.get("type") for b in resposta["content"]]
            print(f"{foto.name}: resposta sem bloco de texto (blocos: {tipos}), não salva")
            continue
        extracao = json.loads(texto)
        destino.write_text(json.dumps({"modelo": resposta.get("model"), "uso": uso, "extracao": extracao},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{foto.name}: status={extracao['status']}, {len(extracao['itens'])} itens")

    gerar_conferencia(pasta_json, pasta_saida)
    print(f"\nTokens desta rodada: {entrada} de entrada, {saida} de saída.")
    print(f"Planilhas de conferência em: {pasta_saida}")


if __name__ == "__main__":
    main()
