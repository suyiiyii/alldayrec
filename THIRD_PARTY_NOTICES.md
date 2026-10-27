# Third-party notices

AllDayRec is MIT-licensed ([LICENSE](LICENSE)). The parts below come from others and keep their own licenses.

## Code in this repository

| Path | From | License |
|---|---|---|
| `firmware/devkit/` | The [Omi](https://github.com/BasedHardware/omi) devkit firmware (`omi/firmware/devkit` at 2e878f6), Copyright (c) 2024 Based Hardware Contributors; our changes on top | MIT, [`firmware/devkit/LICENSE`](firmware/devkit/LICENSE) |
| `firmware/devkit/src/lib/opus-1.2.1/` | [Opus](https://opus-codec.org/) 1.2.1, Copyright 2001-2011 Xiph.Org, Skype Limited, Octasic, Jean-Marc Valin, Timothy B. Terriberry, CSIRO, Gregory Maxwell, Mark Borgerding, Erik de Castro Lopo | BSD-3-Clause, [`COPYING`](firmware/devkit/src/lib/opus-1.2.1/COPYING) |
| `firmware/devkit/src/lib/battery/` | Battery driver, Copyright 2024 Marcus Alexander Tjomsaas | Apache-2.0 (below) |
| `firmware/devkit/src/sdhc_spi.c` | Zephyr's `drivers/sdhc/sdhc_spi.c` from nRF Connect SDK v2.7.0, Copyright 2022,2024 NXP; changed, each change marked `LR:` | Apache-2.0 (below) |
| `firmware/arcs-mini3/sdk.patch` | Changes to ListenAI's ARCS SDK `arcs_mini` (release mini-v3.1.0, `b8631d37`). Its context lines are ListenAI's application code (`apps/arcs-mini/`, `src/`, no license header) and [noPoll](https://www.aspl.es/nopoll/) (`arcs-sdk/modules/nopoll/`), Copyright (C) 2024 Advanced Software Production Line, S.L. | The patched files' own terms; noPoll: [LGPL-2.1-or-later](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) |
| `server/alldayrec/vad_postprocessor.py` | [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) 0.0.2 `fireredvad/core/stream_vad_postprocessor.py`, unchanged, Copyright 2026 Xiaohongshu | Apache-2.0 (below) |
| `app/android/app/src/main/kotlin/com/suyiiyii/live_record/Bean.kt` (the Feishu recorder bean's protocol: framing, keys, file and audio decryption, parsers) | Ported from [ws/soundcore](https://github.com/ws/soundcore) (`crates/soundcore-d3200-protocol`), Copyright (C) 2026 Will Smidlein, and [Eliqi797/open-feishu-recording-bean](https://github.com/Eliqi797/open-feishu-recording-bean) (`android/.../D3200Protocol.kt`, `DeviceCrypto.kt`), Copyright (c) 2026 Eliqi797. Not affiliated with Anker or Feishu | 0BSD and MIT (below) |
| `fixtures/bean-crypto.json` | ws/soundcore's `crates/soundcore-d3200-protocol/tests/crypto.json` (byte vectors), unchanged, Copyright (C) 2026 Will Smidlein | 0BSD (below) |
| `web/src/components/Icon.vue` | The SVG paths of 29 [Lucide](https://lucide.dev) icons, lucide-static 0.460.0, unchanged | ISC (below) |
| `docs/diagrams/architecture.drawio` and the images exported from it | 14 [Lucide](https://lucide.dev) icons, lucide-static 0.460.0, with the stroke colour set | ISC (below) |

## Model files (not in the repository; the server downloads them, `server/alldayrec/infer.py`)

- **FireRedVAD** Stream-VAD ONNX and its CMVN stats, by FireRedTeam, downloaded from the FireRedVAD repository: Apache-2.0.
- **ReDimNet2 B6** (`redimnet2-b6.onnx`), the voiceprint model, by [PalabraAI](https://github.com/PalabraAI/redimnet2): code and weights MIT. Exported to ONNX by `server/alldayrec/export_onnx.py` from the release weights `b6-vb2+vox2+cnc2_v0-lm.pt`; the server downloads that export from this repository's `models-v1` release, which carries this same notice. **Its training data includes VoxBlink2, licensed CC BY-NC-SA 4.0** (non-commercial, share-alike); the other training sets are VoxCeleb2 and CN-Celeb. Check whether that fits your use; you can always make the file yourself with `export_onnx.py`.
- **YAMNet** (`yamnet.onnx`), the music classifier, by Google ([tensorflow/models](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet), trained on AudioSet): code and weights Apache-2.0. Converted to ONNX as `server/alldayrec/export_onnx.py` describes; the server downloads it from this repository's `models-v1` release.

## Fetched at build time (not in the repository)

- **nRF Connect SDK** v2.7.0 (`sdk-nrf`: LicenseRef-Nordic-5-Clause, for use with Nordic Semiconductor devices only; the firmware runs on the nRF52840) and **Zephyr** (Apache-2.0), for `firmware/devkit` and `firmware/selftest`.
- **ESP-IDF** (Apache-2.0), for `firmware/blerelay`.
- **ListenAI ARCS SDK** `arcs_mini` mini-v3.1.0 and its toolchain, for `firmware/arcs-mini3`: `arcs-sdk/` is Apache-2.0, the other modules carry their own LICENSE files.
- The Python, npm, Flutter, Gradle and Swift packages in the lockfiles, each under its own license (MIT, BSD or Apache-2.0 for the server and web dependencies), among them Nordic's Android and iOS DFU libraries (BSD-3-Clause). On Android, `mobile_scanner` pulls in Google ML Kit, under Google's terms.

Firmware binaries (UF2 or DFU zip) contain Opus and the nRF Connect SDK: distribute them with the Opus notice above and the Nordic license.

## Apache License 2.0

```text

                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
```

## ISC License (Lucide)

```text
ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## Zero-Clause BSD License (ws/soundcore)

```text
Zero-Clause BSD License

Copyright (C) 2026 Will Smidlein

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## MIT License (Eliqi797/open-feishu-recording-bean)

```text
MIT License

Copyright (c) 2026 Eliqi797

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
