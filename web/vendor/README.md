# Vendored Three.js

These files are Three.js **r160** (`0.160.0`), MIT licensed, vendored so the
studio works without a CDN or an npm install.

| file | source |
|---|---|
| `three.module.js` | `three/build/three.module.js` |
| `addons/controls/OrbitControls.js` | `three/examples/jsm/controls/OrbitControls.js` |
| `addons/loaders/GLTFLoader.js` | `three/examples/jsm/loaders/GLTFLoader.js` |
| `addons/environments/RoomEnvironment.js` | `three/examples/jsm/environments/RoomEnvironment.js` |
| `addons/utils/BufferGeometryUtils.js` | `three/examples/jsm/utils/BufferGeometryUtils.js` |

The addon imports were rewritten from the bare specifier `"three"` to the
relative path `"../../three.module.js"`, so no import map is required. Keep the
license headers intact when updating.

Upstream: <https://github.com/mrdoob/three.js/tree/r160>
