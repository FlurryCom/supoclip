# Tirar música com direitos autorais dos clipes (Cat Clips)

Data: 2026-11-22 · Status: rascunho para o dono aprovar

## Intenção

Vários clipes estão sendo bloqueados por direitos autorais nas redes: o rádio do GTA (e qualquer música que toca
na live) vai junto no áudio. O dono quer que a música saia do clipe antes de postar, mantendo a voz do streamer,
para os clipes continuarem indo para as redes.

## Metas

- Todo clipe feito com um kit Cat Clips (lives e Reformular) passa por um detector de música antes dos sons do kit
  e do CTA.
- Nos trechos com música, a música sai e as vozes (streamer, amigo, falas do jogo) ficam.
- Se depois da limpeza ainda sobrar música (em geral, música cantada: a voz do cantor fica junto com as vozes),
  o clipe **não vai para as redes**: fica na Biblioteca, e o post aparece como recusado com o motivo
  "música com direitos autorais".
- Fora dos trechos com música, o áudio fica igual ao de hoje.

## Fora do escopo

- Vídeos já postados e bloqueados: o dono apaga nas redes.
- Pôr outra música no lugar.
- Descobrir pelo Postiz quais posts foram bloqueados depois de publicados.

## Abordagens consideradas

- **A — Detectar e tirar a música, mantendo as vozes (escolhida pelo dono).**
- B — Detectar e só não postar: simples, mas perderia boa parte dos clipes de GTA (rádio no carro o tempo todo).

## Desenho

1. **Detector de música** (`backend/src/media/music_check.py`, novo): classificador de sons do AudioSet
   (PANNs, roda com o `torch` que já está instalado, na CPU) em janelas de ~2 s com passo de 1 s. Dá, por
   segundo do clipe, a chance de "música". Trechos de música = segundos acima do limiar, juntando buracos curtos.
2. **Separador** (mesmo módulo): só se houver trecho de música. Um modelo de separação de vozes (Demucs ou
   MDX-Net, o que rodar sem dependência pesada nova) separa o áudio do clipe em "vozes" e "resto". Nos trechos de
   música entra só "vozes", com transição suave de ~0,3 s; fora deles, o áudio original. O vídeo não é
   reencodado: só o áudio é trocado.
3. **Conferência:** o detector roda de novo nos trechos limpos. Se ainda der música acima do limiar, o clipe é
   marcado "música restante".
4. **Onde fica salvo:** uma linha por arquivo do clipe (como o `live_clip_sensitive`), com o resultado: `clean`
   (sem música), `removed` (música tirada), `left` (sobrou música) ou `error`.
5. **Postagem** (recorder, `post_clips` e Reformular): clipe com `left` → post recusado em todas as contas com o
   motivo "música com direitos autorais" (fica registrado, nunca tenta de novo). Vale também para o botão
   Publicar da Biblioteca.
6. **Onde liga:** chave `"music_clean": True` nos kits `catclips` e `catclips_v2`; roda em
   `video_service` logo depois de `create_optimized_clip` e antes de `apply_edit_kit`.

## Decisões minhas (para o dono conferir)

- **Se a checagem falhar** (modelo não baixou, erro no áudio): o clipe segue como hoje, com aviso no log. Segurar
  todos os clipes quando o modelo quebra pararia a postagem inteira.
- **Falas do jogo e barulhos do jogo** (tiro, motor) dentro do trecho com música somem junto com a música; as
  vozes ficam. Fora desses trechos, nada muda.
- **Clipe recusado por música** também é recusado no Publicar manual. Se o dono quiser forçar, fica para depois.

## Restrições

- Tempo: alvo < 60 s a mais por clipe de 1–2 min (CPU). Clipe sem música: só o detector (< 10 s).
- Memória: o dono reclamou de RAM; pico < 2 GB a mais por clipe, modelos carregados uma vez por worker.
- Modelos baixados uma vez para `/app/uploads/models/` (fora do git), com checagem de hash, como o modelo de voz.
- Nada pago, sem chaves.
- Tudo que o dono lê em português.

## Riscos

- Música cantada é o caso mais comum no rádio do GTA: muitos clipes podem cair em `left`. A prévia real mostra
  quantos; se forem muitos, dá para testar um modelo de separação melhor.
- Música baixinha de fundo pode passar abaixo do limiar e o Content ID pegar mesmo assim. O limiar é ajustado na
  prévia com clipes reais que foram bloqueados.

## Deixado para quem constrói

- Qual modelo PANNs (CNN14 ou MobileNet) e qual separador, desde que caibam nas restrições.
- Limiares (chance de música, buraco mínimo, trecho mínimo), ajustados com 2–3 clipes reais de GTA com rádio.
- Prévia para o dono (antes/depois de 2–3 clipes) antes de ligar no kit.
