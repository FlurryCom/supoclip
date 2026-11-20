# Legenda do streamer × legenda do jogo (Cat Clips v2)

Data: 2026-11-20 · Status: aguardando aprovação do dono

## Intenção

Nos clipes de live, a voz do streamer e as falas do jogo (personagens, rádio, cutscenes) chegam misturadas
no mesmo áudio, e hoje viram uma legenda só, no visual Cat Clips. O dono quer distinguir as duas:
a fala do streamer continua no visual Cat Clips, a fala do jogo aparece num estilo diferente — e, quando o
próprio jogo já está mostrando a legenda dele naquele momento, a nossa legenda do jogo não aparece
(não repetir o que já está escrito na tela).

## Metas

- Cada palavra da legenda é marcada como **streamer** ou **jogo**.
- **Streamer**: visual Cat Clips de hoje (Archivo Black, creme, destaque amarelo palavra a palavra).
- **Jogo**: branco, menor (~75 % do tamanho), contorno preto, sem destaque amarelo nem "pulo" da palavra;
  mesmo lugar (logo abaixo da faixa amarela). Decisão minha, aprovada na conversa.
- **Legenda do jogo já na tela** naquele trecho → as palavras do jogo desse trecho não aparecem.
- Vale para o kit **Cat Clips v2** (lives e Reformular de clipes deitados). O Cat Clips original não muda.

## Fora do escopo

- Separar mais de "streamer" e "resto" (não vamos dar cor por personagem).
- Traduzir ou corrigir a legenda do jogo.
- Vídeos em pé do Reformular (sem câmera separada): continuam com uma legenda só.

## Abordagens consideradas

- **A — Voz + boca (escolhida pelo dono).** Um modelo pequeno de "impressão digital de voz" (ONNX, ~30 MB,
  roda na CPU com o `onnxruntime` que já está instalado; sem torchaudio) dá um vetor por trecho de fala;
  os trechos são agrupados por voz. A voz do streamer é a que fala enquanto a boca dele se mexe na câmera
  (o v2 já acha a câmera); sem câmera, a voz que mais fala no clipe.
- B — Só a boca: sem instalar nada, mas erra quando o streamer ri, mexe a cabeça ou não há câmera.
- C — Transcrição paga com separação de vozes (AssemblyAI): custo por minuto; o projeto usa Whisper local.

## Desenho

1. **Rótulo por palavra** (`backend/src/media/voice_roles.py`, novo):
   - Junta as palavras do clipe em trechos contínuos de fala (pausa > 0,6 s separa trechos; trecho mínimo ~1 s
     para ter vetor de voz; trechos curtos herdam o rótulo do vizinho mais próximo).
   - Vetor de voz por trecho com o modelo ONNX; agrupamento por similaridade de cosseno (limiar fixo,
     ajustado numa prévia real), sem dependência nova além do modelo.
   - **Qual grupo é o streamer:** para cada grupo, quanto a boca do rosto da câmera se mexe durante as falas
     dele (diferença entre quadros na metade de baixo do rosto) comparado com os momentos em que ele não fala.
     O grupo com a maior diferença é o streamer. Sem câmera ou sem diferença clara: o grupo com mais tempo
     de fala.
   - Falha em qualquer etapa (modelo ausente, áudio estranho) → todas as palavras ficam "streamer"
     (exatamente como hoje). Nunca derruba o clipe.
2. **Legenda do jogo já na tela:** para cada trecho do jogo, olhar 2–3 quadros da área do jogo (parte de
   baixo do quadro original) com `source_captions.caption_lines_in_frame`, que já existe. Se houver texto de
   legenda, as palavras daquele trecho somem.
3. **Legenda** (`media/captions.py`): as palavras chegam com `role`; os grupos de palavras nunca misturam
   streamer e jogo (quebra de bloco na troca); blocos do jogo usam um estilo ASS "Game" com as cores e o
   tamanho acima, sem caixa amarela e sem animação de pulo.
4. **Onde liga:** só quando o kit tem `"voice_roles": True` (o Cat Clips v2 passa a ter). O modelo é baixado
   uma vez e guardado junto dos outros modelos do backend.

## Decisões minhas (para o dono conferir)

- **Editor de clipes:** se o dono editar um clipe à mão no editor, a legenda volta a ser uma só (o editor não
  guarda quem falou). Clipes de live raramente são editados; guardar isso no editor dobraria o trabalho.
- **Como liga:** primeiro sai uma prévia feita fora do fluxo, com lives reais já gravadas no disco
  (`/app/uploads/uploads`). Só depois da aprovação o Cat Clips v2 passa a ter `voice_roles`, valendo para
  todos os canais e o Reformular de uma vez.
- **Ajuste dos limiares:** com 2–3 lives reais desses arquivos, de preferência uma com cutscene e uma com
  amigo/co-host falando.

## Restrições

- Não pode atrasar muito o clipe: alvo < 10 s a mais por clipe de 1–2 min (CPU).
- Sem chaves/segredos; nada de serviço pago.
- Tudo que o dono lê em português.

## Riscos

- Cutscene longa com o streamer calado e mexendo a boca (cantando, rindo) pode inverter os rótulos num
  trecho. A prévia com lives reais mostra se o limiar precisa de ajuste.
- Lives com várias pessoas na sala (duas vozes "do streamer"): a segunda voz vira "jogo".

## Deixado para quem constrói

- Qual modelo ONNX de voz (CAM++ do 3D-Speaker ou ResNet do WeSpeaker) e o cálculo das features (fbank)
  em numpy.
- Limiar de agrupamento e da diferença de boca, ajustados com 2–3 lives reais antes de ligar.
- Prévia para o dono (começo de um clipe com fala do jogo e do streamer) antes de ligar no v2 de todos os canais.
