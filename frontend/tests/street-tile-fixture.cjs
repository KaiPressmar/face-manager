// A small Mapbox Vector Tile 2.1 sample with tags declared after the features.
function varint(number) {
  const bytes = [];
  while (number >= 128) {
    bytes.push((number % 128) | 128);
    number = Math.floor(number / 128);
  }
  return Buffer.from([...bytes, number]);
}

function bytesField(field, bytes) {
  return Buffer.concat([varint(field * 8 + 2), varint(bytes.length), bytes]);
}

const numberField = (field, number) => Buffer.concat([varint(field * 8), varint(number)]);
const stringField = (field, string) => bytesField(field, Buffer.from(string));
const packed = (field, numbers) => bytesField(field, Buffer.concat(numbers.map(varint)));

function layer(name, type, geometry, props) {
  const entries = Object.entries(props);
  const feature = Buffer.concat([
    packed(2, entries.flatMap((_, index) => [index, index])), numberField(3, type), packed(4, geometry),
  ]);
  return bytesField(3, Buffer.concat([
    numberField(15, 2), stringField(1, name), bytesField(2, feature),
    ...entries.map(([key]) => stringField(3, key)),
    ...entries.map(([, value]) => bytesField(4, stringField(1, value))),
    numberField(5, 4096),
  ]));
}

exports.streetTileFixture = () => Buffer.concat([
  layer('transportation', 2, [9, 0, 4096, 10, 8192, 0], { class: 'primary' }),
  layer('transportation_name', 2, [9, 0, 4096, 10, 8192, 0], { name: 'Example Avenue', 'name:de': 'Beispielstraße' }),
  layer('building', 3, [9, 1800, 1800, 26, 200, 0, 0, 200, 199, 0, 15], {}),
]);
