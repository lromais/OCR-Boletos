import random
from datetime import date
from decimal import Decimal

from boletos.barcode import (barras_para_linha47, barras_para_linha48, mod10,
                             mod11_arrecadacao, mod11_cobranca, validar)
from boletos.extractor import extrair_boletos


def gera_bancario(banco="341", fator="1500", valor="0000012345", livre=None):
    livre = livre or "".join(random.choice("0123456789") for _ in range(25))
    sem_dv = banco + "9" + fator + valor + livre
    dv = mod11_cobranca(sem_dv)
    return sem_dv[:4] + str(dv) + sem_dv[4:]


def gera_arrecadacao(ident="6"):
    corpo = "8" + "2" + ident + "00000015990" + "".join(random.choice("0123456789") for _ in range(29))
    fn = mod10 if ident in "67" else mod11_arrecadacao
    dv = fn(corpo)
    return corpo[:3] + str(dv) + corpo[3:]


def test_exemplo_publico_banco_do_brasil():
    r = validar("00190.50095 40144.816069 06809.350314 3 37370000000100")
    assert r.valido, r.erros
    assert r.banco == "001"
    assert r.valor == Decimal("1.00")


def test_roundtrip_bancario():
    for _ in range(200):
        barras = gera_bancario()
        linha = barras_para_linha47(barras)
        rb, rl = validar(barras), validar(linha)
        assert rb.valido and rl.valido
        assert rl.codigo_barras == barras
        assert rb.valor == Decimal("123.45")


def test_vencimento_novo_ciclo():
    # fator 1000 após 22/02/2025 = 22/02/2025
    r = validar(gera_bancario(fator="1000"), referencia=date(2025, 3, 1))
    assert r.vencimento == date(2025, 2, 22)
    # mesmo fator, e-mail antigo => ciclo 1 (03/07/2000)
    r = validar(gera_bancario(fator="1000"), referencia=date(2000, 6, 1))
    assert r.vencimento == date(2000, 7, 3)


def test_digito_errado_reprova():
    linha = barras_para_linha47(gera_bancario())
    errado = linha[:12] + str((int(linha[12]) + 1) % 10) + linha[13:]
    r = validar(errado)
    assert not r.valido and r.erros


def test_arrecadacao_mod10_e_mod11():
    for ident in "6789":
        for _ in range(50):
            barras = gera_arrecadacao(ident)
            linha = barras_para_linha48(barras)
            assert validar(barras).valido
            r = validar(linha)
            assert r.valido, (ident, r.erros)
            assert r.codigo_barras == barras
    assert validar(gera_arrecadacao("6")).valor == Decimal("159.90")


def test_extrai_de_texto_ocr_ruidoso():
    linha = barras_para_linha47(gera_bancario())
    fmt = f"{linha[:5]}.{linha[5:10]} {linha[10:15]}.{linha[15:21]} {linha[21:26]}.{linha[26:32]} {linha[32]} {linha[33:]}"
    ruido = fmt.replace("0", "O", 1)
    texto = f"Banco Itaú\nPagável em qualquer banco\n{ruido}\nVencimento 10/12/2026\nCNPJ 12.345.678/0001-90"
    achados = extrair_boletos(texto)
    assert len(achados) == 1
    assert achados[0].linha_digitavel == linha


def test_texto_sem_boleto():
    assert extrair_boletos("Telefone 11 99999-9999 CPF 123.456.789-00 pedido 1234567890123") == []
