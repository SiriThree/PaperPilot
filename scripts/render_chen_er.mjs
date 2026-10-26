/** Optional documentation renderer. Install @viz-js/viz in .runtime/er-review. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const directory = path.join(root, 'docs/data-model/diagrams');
const model = JSON.parse(fs.readFileSync(path.join(directory, 'chen-model.json'), 'utf8'));
const entry = path.join(root, '.runtime/er-review/node_modules/@viz-js/viz/dist/viz.js');
if (!fs.existsSync(entry)) {
  throw new Error('Run npm.cmd install --prefix .runtime/er-review @viz-js/viz first.');
}
const { instance } = await import(pathToFileURL(entry).href);
const viz = await instance();
const quote = value => JSON.stringify(value);
const html = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

function attributeLabel(attribute) {
  const chinese = html(attribute.label);
  const english = html(attribute.name);
  return `<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD>${
    attribute.key ? `<U>${chinese}</U>` : chinese
  }</TD></TR><TR><TD><FONT POINT-SIZE="10">${english}${attribute.nullable ? ' ?' : ''}</FONT></TD></TR></TABLE>`;
}

// A small view has a hand-arranged layout so its three relationships stay unambiguous.
function identitySvg(view) {
  const items = [
    '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1180" viewBox="0 0 1600 1180">',
    '<rect width="1600" height="1180" fill="white"/>',
    '<g font-family="Microsoft YaHei, sans-serif" fill="white" stroke="#111" stroke-width="1.5">',
    '<path d="M340 350H580 M720 350H1010 M250 392V900H580 M720 900H1010 M1100 392V600 M1100 700V858" fill="none"/>',
  ];
  const layouts = {
    USER: [250, 350, [[90, 130], [270, 130], [450, 130]]],
    PROJECT: [1100, 350, [[850, 130], [1100, 130], [1350, 130], [1460, 350]]],
    MEMBERSHIP: [1100, 900, [[850, 1060], [1100, 1060], [1350, 1060]]],
  };
  for (const name of view.entities) {
    const [x, y, positions] = layouts[name];
    const entity = model.entities[name];
    if (positions.length !== entity.attributes.length) throw new Error(`Update identity layout for ${name}`);
    entity.attributes.forEach((attribute, index) => {
      const [ax, ay] = positions[index];
      items.push(`<line x1="${x}" y1="${y}" x2="${ax}" y2="${ay}"/>`);
    });
  }
  for (const name of view.entities) {
    const [x, y, positions] = layouts[name];
    const entity = model.entities[name];
    items.push(`<rect x="${x - 90}" y="${y - 42}" width="180" height="84"/>`);
    items.push(`<text x="${x}" y="${y - 4}" text-anchor="middle" fill="#111" stroke="none" font-size="21">${html(entity.label)}</text>`);
    items.push(`<text x="${x}" y="${y + 22}" text-anchor="middle" fill="#111" stroke="none" font-size="15">${name}</text>`);
    entity.attributes.forEach((attribute, index) => {
      const [ax, ay] = positions[index];
      items.push(`<ellipse cx="${ax}" cy="${ay}" rx="89" ry="39"/>`);
      items.push(`<text x="${ax}" y="${ay - 4}" text-anchor="middle" fill="#111" stroke="none" font-size="18"${attribute.key ? ' text-decoration="underline"' : ''}>${html(attribute.label)}</text>`);
      items.push(`<text x="${ax}" y="${ay + 19}" text-anchor="middle" fill="#111" stroke="none" font-size="12">${html(attribute.name)}</text>`);
    });
  }
  for (const [x, y, label] of [[650, 350, '负责'], [650, 900, '参与'], [1100, 650, '包含']]) {
    items.push(`<polygon points="${x - 70},${y} ${x},${y - 50} ${x + 70},${y} ${x},${y + 50}"/>`);
    items.push(`<text x="${x}" y="${y + 7}" text-anchor="middle" fill="#111" stroke="none" font-size="21">${label}</text>`);
  }
  for (const [x, y, label] of [[450, 327, '1'], [880, 327, 'N'], [275, 650, '1'], [850, 877, 'N'], [1124, 505, '1'], [1124, 795, 'N']]) {
    items.push(`<text x="${x}" y="${y}" fill="#111" stroke="none" font-size="23">${label}</text>`);
  }
  items.push(`<text x="800" y="50" text-anchor="middle" fill="#111" stroke="none" font-size="27">${html(view.title)}</text>`);
  items.push('<text x="800" y="1154" text-anchor="middle" fill="#444" stroke="none" font-size="15">矩形：实体　椭圆：属性　菱形：联系　下划线：主键　1/N：最大联系基数，最小数量见联系表</text>');
  items.push('</g></svg>\n');
  return items.join('\n');
}

for (const view of model.views) {
  const lines = [
    'graph ER {',
    'graph [layout=dot, rankdir=TB, compound=true, newrank=true, nodesep=0.25, ranksep=0.65,',
    '  splines=polyline, outputorder=edgesfirst, bgcolor="white", pad=0.3,',
    `  fontname="Microsoft YaHei", fontsize=23, labelloc=t, label=${quote(view.title + '\n矩形：实体　椭圆：属性　菱形：联系　下划线：主键　?：可空　1/N：最大联系基数')}];`,
    'node [fontname="Microsoft YaHei", fontsize=16, color="#111111", fillcolor="white", style=filled, penwidth=1.2];',
    'edge [dir=none, color="#333333", penwidth=1.1, fontname="Microsoft YaHei", fontsize=18, labeldistance=1.8];',
  ];
  view.entities.forEach((name, index) => {
    const entity = model.entities[name];
    lines.push(`subgraph cluster_${index} { label=""; color="transparent"; margin=14;`);
    lines.push(`e_${name} [shape=box, margin="0.18,0.15", label=${quote(entity.label + '\n' + name)}];`);
    const top = [], bottom = [];
    entity.attributes.forEach((attribute, number) => {
      const id = `a_${name}_${number}`;
      const isTop = number < Math.ceil(entity.attributes.length / 2);
      (isTop ? top : bottom).push(id);
      lines.push(`${id} [shape=ellipse, margin="0.10,0.04", label=<${attributeLabel(attribute)}>];`);
      lines.push(isTop ? `${id} -- e_${name} [weight=2];` : `e_${name} -- ${id} [weight=2];`);
    });
    if (top.length) lines.push(`{ rank=same; ${top.join('; ')}; }`);
    if (bottom.length) lines.push(`{ rank=same; ${bottom.join('; ')}; }`);
    lines.push('}');
  });
  const outgoing = new Map(), incoming = new Map();
  view.relations.forEach((relation, number) => {
    const left = relation.left_cardinality.endsWith('N') ? 'N' : '1';
    const right = relation.right_cardinality.endsWith('N') ? 'N' : '1';
    const id = `r_${number}`;
    const out = outgoing.get(relation.left) || 0;
    const inc = incoming.get(relation.right) || 0;
    outgoing.set(relation.left, out + 1);
    incoming.set(relation.right, inc + 1);
    const tailport = ['se', 'sw', 'e', 'w', 's', 'n'][out % 6];
    const headport = ['nw', 'ne', 'w', 'e', 'n', 's'][inc % 6];
    lines.push(`${id} [shape=diamond, margin="0.12,0.06", label=${quote(relation.label)}];`);
    lines.push(`e_${relation.left} -- ${id} [tailport=${tailport}, label=${quote(left)}, minlen=1, weight=5];`);
    lines.push(`${id} -- e_${relation.right} [headport=${headport}, label=${quote(right)}, minlen=1, weight=5];`);
  });
  lines.push('}');
  const dot = lines.join('\n') + '\n';
  const svg = view.name === 'v1-identity-project'
    ? identitySvg(view)
    : viz.renderString(dot, { format: 'svg', engine: 'dot' });
  fs.writeFileSync(path.join(directory, view.name + '.svg'), svg);
  console.log(`${view.name}: ${view.entities.length} entities, ${view.relations.length} relationships`);
}
