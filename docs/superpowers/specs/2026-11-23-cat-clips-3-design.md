# Cat Clips 3.0: jogo na largura do celular, fala do NPC em cima dele, barra de Vice City

Data: 2026-11-23 · Status: aprovado pelo dono (2026-11-23)

## Intenção

O dono viu um estilo de clipe que achou mais limpo e mais focado no personagem: a câmera do streamer em
cima e, embaixo, o jogo **cortado na largura do celular**, e não o quadro 16:9 inteiro sobre um fundo
borrado, como no v2. A legenda do streamer entra palavra por palavra, com animação. A fala dos personagens
do jogo (NPCs) aparece **em cima do personagem**.

O v2 continua existindo. O 3.0 é um kit novo (`catclips_v3`, nome "Cat Clips 3.0") que cada canal pode
escolher.

## Metas

Saída 1080×1920, de cima para baixo:

| Área | Altura | O que mostra |
|---|---|---|
| Webcam | 640 px (1/3) | a webcam do streamer, pelas caixas do canal (enquadramento por canal + checagem do Claude) |
| Barra do título | 200 px | título em **Pricedown**, branco com contorno escuro, sobre a imagem de **palmeiras de Vice City** |
| Jogo | 1080 px | o jogo cortado na largura do celular, **seguindo a ação**, com as legendas |

**Webcam**
- Mesma regra do v2: as caixas marcadas do canal mandam; sem caixas, entra o detector.
- Sem webcam, o topo mostra o jogo desfocado, como no v2.

**Barra do título**
- Fonte Pricedown Black **sem ligaduras**. Com elas ligadas, o N e o S ganham um traço extra, e o dono não
  gostou.
- Fundo: a imagem de palmeiras que o dono mandou, recortada no meio (opção "B · Meio" da prévia, mais ou
  menos 55% da altura da imagem).
- O título quebra em até 2 linhas e diminui para caber, como no v2.

**Jogo**
- Recorte quadrado da fonte, com a altura sem a faixa de baixo. Nessa faixa o GTA põe a legenda dele, que
  sairia cortada pela metade no recorte.
- Ampliado para 1080×1080.
- Corte **fixo no centro** (decisão do dono, 2026-11-23): o GTA já mantém o personagem controlado no meio da
  tela. Acompanhar a ação de um lado para o outro fica para depois, se os clipes de teste pedirem. Precisaria
  de um detector de corpo.

**Legenda do streamer**
- Palavra por palavra. A palavra falada entra com um "pulo" rápido (cresce um pouco e volta) e fica amarela
  e maior.
- 2 a 3 palavras na tela por vez, no alto do jogo.
- Maior que a legenda de NPC.
- A fala de um amigo (voz em call) segue o mesmo estilo, com a cor de amigo do v2.

**Fala de NPC**
- A voz "game" do separador de vozes do v2 aparece **sempre**, inclusive quando o GTA mostra a legenda
  dele. Isso muda a regra do v2, que escondia essas falas.
- A legenda fica em cima da cabeça do NPC que está falando e acompanha o NPC se ele se mover.
- **Qual NPC:** a boca que mexe junto com a voz, o mesmo truque que acha o streamer na webcam. Sem como
  decidir, fica o personagem mais perto do centro.
- **NPC ainda fora da tela:** como o clipe já está gravado, o render olha para a frente. Se o NPC aparece
  depois, a fala até lá fica na **borda do lado onde ele vai aparecer** e acompanha quando ele entra.
- **Nenhum NPC achado no clipe inteiro:** a fala vai para um lugar fixo no meio do jogo, com a cor de NPC.
- **Sem modelo novo** (decisão do dono, 2026-11-23): os NPCs são achados pelo detector de rostos que já existe.
  Ele acha bem em conversas e cutscenes e quase nunca de longe ou de costas. Nesses casos vale a regra da
  borda ou o lugar fixo. Um detector de corpo pode vir depois dos clipes de teste.

## Fora do escopo

- Filtrar clipes por tipo (engraçado, polêmica, reclamação). É um projeto separado, com desenho próprio,
  depois deste.
- Mexer no v2 ou nos outros kits.
- Animações além do "pulo" da palavra: emojis, efeitos e transições.

## Anti-metas

- Uma consulta ao Claude por clipe ou por fala só para posicionar legenda: o custo e o tempo de render
  subiriam.
- Fala de NPC tapando a webcam ou a barra do título: ela fica sempre dentro da área do jogo.
- O corte seguir o rosto da webcam, que também aparece no quadro do jogo.
- Pôr o arquivo da fonte ou a imagem das palmeiras no GitHub. A licença da Pricedown proíbe compartilhar o
  arquivo, e a imagem é de terceiros. Os dois ficam só na máquina.

## Abordagens consideradas (o dono escolheu as recomendadas)

**Corte do jogo**
- **Corte fixo no centro (escolhida depois da medição).** O rastreador que existe segue rostos, e o
  protagonista aparece de costas.
- Seguir o personagem com um detector de corpo novo: fica para depois, se os clipes de teste pedirem.
- Corte fixo no centro: perde a ação na lateral.
- Claude decidir o corte de cada clipe: lento e caro.

**Fala de NPC**
- **Detector de rostos no jogo + boca mexendo com a voz + olhar para a frente (escolhida).**
- Lugar fixo para toda fala de NPC: não é o que o dono pediu. Fica só como último recurso.
- Claude marcar o NPC em cada fala: mais certeiro, mas lento e caro.

**Título**
- **Imagem pronta, gerada com a fonte sem ligaduras e sobreposta (escolhida).** O desenhador de texto do
  ffmpeg não tem como desligar ligaduras de forma confiável.

## Restrições

- Arquivos que ficam só na máquina, fora do git, no disco de dados que os containers já montam
  (`/app/uploads`):
  - fonte: `CATCLIPS3_TITLE_FONT`, padrão `/app/uploads/kit-assets/pricedown.otf`;
  - fundo: `CATCLIPS3_TITLE_BG`, padrão `/app/uploads/kit-assets/vice-city-palms.webp`.
- Se faltar um dos dois, a barra cai no estilo do v2 (Archivo Black sobre amarelo) e o log avisa. O render
  nunca quebra por isso.
- Qualquer falha ao posicionar a fala de NPC manda a fala para o lugar fixo. Qualquer falha no corte que
  segue a ação usa o corte do centro.
- Reaproveitar o que já existe: caixas e checagem de enquadramento por canal (`media/framing.py`),
  separador de vozes (`media/voice_roles.py`), legendas em ASS/libass (`media/captions.py`), rastreador e
  detector de rostos (`media/reframing.py`).
- O editor de enquadramento da página de Lives, hoje visível só para o v2, passa a aparecer também para o
  3.0. As caixas são as mesmas.

## Decisões tomadas na revisão do desenho (minhas, para o dono conferir)

- **Quais vozes viram fala de NPC:** no 3.0, toda voz que não é do streamer nem de amigo vira fala de NPC,
  inclusive as "incertas" que o v2 descartava. Sem isso elas sumiriam, porque a legenda do GTA sai do
  recorte. A fala do próprio protagonista também conta como NPC e segue a mesma regra de posição.
- **Recorte do jogo e a webcam:** o recorte vem de dentro da caixa de jogo do canal (o quadro inteiro, se não
  houver caixa) e evita a caixa da webcam, para o streamer não aparecer duas vezes. A faixa de baixo cortada
  é medida dentro dessa caixa.
- **Fonte e fundo:** a Pricedown (pacote gratuito da Typodermic que o dono baixou) e a imagem de palmeiras
  que ele mandou vão para `kit-assets` no disco de dados, junto com a construção.
- **Aprovação:** o 3.0 fica fora da lista de kits dos canais até o dono aprovar os clipes de teste. Esses
  clipes são feitos pelo fluxo "reformular", que já re-renderiza um clipe em outro kit.

## Medido antes de construir (2026-11-23)

18 trechos reais de GTA (xqc, angryginge13 e caseoh_), 1.080 frames amostrados:
- O detector de rostos atual acha um personagem do jogo em 25% dos frames.
- Em conversas e cutscenes, os rostos são grandes (mediana de 172 px de altura), e ele acha em 70% a 100%
  dos frames.
- Com o personagem andando, de costas ou longe, ele quase nunca acha.
- O protagonista aparece de costas, então o rastreador de rostos não tem o que seguir. O GTA já mantém o
  protagonista no centro da tela.

## O que fica a cargo do construtor

- Quanto cortar embaixo para tirar a legenda do GTA (perto de 15% da altura) e o tamanho exato das fontes,
  dentro das proporções acima.
- O estilo da caixa da fala de NPC (fundo escuro translúcido ou só contorno), legível sobre o jogo.
- Quantos frames amostrar para os rostos e para a boca dos NPCs, dentro do tempo de render de hoje mais
  poucos segundos por clipe.
- Como o "pulo" da palavra é feito no ASS (`\t` de escala).

## Como o dono aprova

O dono só aprova vendo um clipe de verdade. Antes de qualquer canal trocar de kit, renderizar 2 ou 3 clipes
reais de GTA no 3.0 (um com NPC falando, um com NPC fora da tela, um sem webcam, se houver) e mandar para
ele ver.
