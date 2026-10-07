# Third-party browser assets

These files are committed so the dashboard does not execute code from a CDN at runtime.
Versions and SHA-256 checksums are recorded to make updates reviewable.

| File | Version | License | Upstream | SHA-256 |
| --- | --- | --- | --- | --- |
| `../js/alpine.min.js` | Alpine.js 3.15.8 | MIT | `https://cdn.jsdelivr.net/npm/alpinejs@3.15.8/dist/cdn.min.js` | `899842782a7fd16fcc2d7a7c877ff9ec159394044c87b158b2ef132786606932` |
| `highlight.min.js` | highlight.js 11.9.0 | BSD-3-Clause | `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js` | `837a6fa5b0c736b52bbde2b2b6190f305da3fc9ed41681db5321507057b5c846` |
| `highlight/github-dark-dimmed.min.css` | highlight.js 11.9.0 | BSD-3-Clause | `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark-dimmed.min.css` | `bc1116bfba58ee83794d53b8bd08e5ab13cba81bf03454cf67d6cfe435033cae` |
| `highlight/github.min.css` | highlight.js 11.9.0 | BSD-3-Clause | `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css` | `3a9a5def8b9c311e5ae43abde85c63133185eed4f0d9f67fea4b00a8308cf066` |
| `marked.min.js` | marked 12.0.2 | MIT | `https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js` | `15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894` |
| `purify.min.js` | DOMPurify 3.2.4 | Apache-2.0 OR MPL-2.0 | `https://cdn.jsdelivr.net/npm/dompurify@3.2.4/dist/purify.min.js` | `8eb41b658831fab175fad9bcd00fcb2d84e0ed3a25a55053d4ecd4444b8b43a0` |

License texts and source are available from the linked upstream packages. Preserve each
asset's license header when replacing or minifying it.
