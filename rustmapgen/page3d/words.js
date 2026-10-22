// The page's words in three languages: the browser's, else English.
const WORDS = {
  es: {
    title: 'Mapa en 3D', loading: 'Cargando el mapa…', failed: 'No se pudo abrir el mapa.', unavailable: 'Este mapa todavía no tiene vista en 3D.',
    noBuild: 'Zonas de no construcción', noBuildNote: 'En rojo, donde el juego no deja construir: alrededor de los monumentos, de las torres de luz y de las entradas a los túneles. Tampoco se puede sobre caminos ni rieles.',
    names: 'Nombres', monuments: 'Monumentos', rocks: 'Rocas', trees: 'Árboles', rails: 'Rieles', roads: 'Caminos', clouds: 'Nubes',
    time: 'Hora', detail: 'Detalle', auto: 'Auto', low: 'Bajo', high: 'Alto', whole: 'Mapa completo', hide: 'Ocultar panel', map: 'Mapa',
    help: 'Arrastra para girar · clic derecho o Shift para mover · rueda para acercar. W A S D vuelan: el mouse mira, E/Q suben y bajan, Shift va más rápido; un clic o Esc para salir. En el teléfono: un dedo gira, dos mueven y acercan.',
    flying: 'Volando · el mouse mira · W A S D · E/Q sube y baja · Shift más rápido · clic o Esc para salir',
    treesNote: 'Los árboles son aproximados: el juego los planta cuando arranca el servidor. Monumentos y rocas son sus formas de colisión, de tu copia del juego.',
    noModels: 'Los monumentos y las rocas todavía no están: se están haciendo de tu copia del juego (unos minutos). Si no aparecen, usa Prepare 3D en el panel.',
  },
  en: {
    title: '3D map', loading: 'Loading the map…', failed: 'The map could not be opened.', unavailable: 'This map has no 3D view yet.',
    noBuild: 'No-build zones', noBuildNote: 'In red, where the game does not let you build: around monuments, power line pylons and tunnel entrances. Roads and rails are no-build too.',
    names: 'Names', monuments: 'Monuments', rocks: 'Rocks', trees: 'Trees', rails: 'Rails', roads: 'Roads', clouds: 'Clouds',
    time: 'Time', detail: 'Detail', auto: 'Auto', low: 'Low', high: 'High', whole: 'Whole map', hide: 'Hide panel', map: 'Map',
    help: 'Drag to turn · right-drag or Shift-drag to move · wheel to zoom. W A S D fly: the mouse looks, E/Q go up and down, Shift is faster; a click or Esc stops. On a phone: one finger turns, two move and pinch.',
    flying: 'Flying · the mouse looks · W A S D · E/Q up and down · Shift faster · click or Esc to stop',
    treesNote: 'Trees are approximate: the game plants them when a server starts. Monuments and rocks are their collision shapes, from your copy of the game.',
    noModels: 'Monuments and rocks are not drawn yet: they are being made from your copy of the game (a few minutes). If they do not come, use Prepare 3D in the panel.',
  },
  pt: {
    title: 'Mapa em 3D', loading: 'Carregando o mapa…', failed: 'Não foi possível abrir o mapa.', unavailable: 'Este mapa ainda não tem visão em 3D.',
    noBuild: 'Zonas sem construção', noBuildNote: 'Em vermelho, onde o jogo não deixa construir: em volta dos monumentos, das torres de energia e das entradas dos túneis. Também não dá para construir em estradas e trilhos.',
    names: 'Nomes', monuments: 'Monumentos', rocks: 'Rochas', trees: 'Árvores', rails: 'Trilhos', roads: 'Estradas', clouds: 'Nuvens',
    time: 'Hora', detail: 'Detalhe', auto: 'Auto', low: 'Baixo', high: 'Alto', whole: 'Mapa inteiro', hide: 'Ocultar painel', map: 'Mapa',
    help: 'Arraste para girar · botão direito ou Shift para mover · roda para aproximar. W A S D voam: o mouse olha, E/Q sobem e descem, Shift vai mais rápido; um clique ou Esc para sair. No celular: um dedo gira, dois movem e aproximam.',
    flying: 'Voando · o mouse olha · W A S D · E/Q sobe e desce · Shift mais rápido · clique ou Esc para sair',
    treesNote: 'As árvores são aproximadas: o jogo as planta quando o servidor inicia. Monumentos e rochas são suas formas de colisão, da sua cópia do jogo.',
    noModels: 'Os monumentos e as rochas ainda não estão: estão sendo feitos da sua cópia do jogo (alguns minutos). Se não aparecerem, use Prepare 3D no painel.',
  },
};

function language() {
  const asked = (navigator.language || 'en').toLowerCase();
  return asked.startsWith('pt') ? 'pt' : asked.startsWith('es') ? 'es' : 'en';
}

export const lang = language();
export const words = WORDS[lang];
