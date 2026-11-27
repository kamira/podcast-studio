//! L1:自寫 SHA-256(FIPS 180-4;CHG-20261128-01 代決 18)。
//!
//! - NIST 測試向量:FIPS 180-2 / 180-4 範例(空訊息、`abc`、448 位元與 896 位元的兩個多區塊訊息、一百萬個 `a`)與
//!   CAVS `SHA256ShortMsg` 的 8~64 位元訊息,共 13 筆(施工時另以 CPython `hashlib` 離線核對)。
//! - 0~130 位元組每一種長度(訊息第 i 個位元組為 `(31 i + 7) mod 256`):涵蓋 55 / 56 / 63 / 64 / 119 / 120 / 128
//!   等補位與多區塊邊界;預期值由 CPython 3.11 `hashlib.sha256` 產生後寫死。
//! - 分段輸入(每種切法)等於一次輸入;回合常數與初始雜湊值以整數立方根 / 平方根重新推導核對。

use project::sha256::{Sha256, digest, hex_digest, initial_hash, round_constants};

/// `(訊息, 摘要)`:NIST 測試向量。
fn nist_vectors() -> Vec<(Vec<u8>, &'static str)> {
    let hex = |s: &str| -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("十六進位"))
            .collect()
    };
    vec![
        (Vec::new(), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        (b"abc".to_vec(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
        (
            b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".to_vec(),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        ),
        (
            b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu"
                .to_vec(),
            "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
        ),
        (vec![b'a'; 1_000_000], "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"),
        (hex("d3"), "28969cdfa74a12c82f3bad960b0b000aca2ac329deea5c2328ebc6f2ba9802c1"),
        (hex("11af"), "5ca7133fa735326081558ac312c620eeca9970d1e70a4b95533d956f072d1f98"),
        (hex("b4190e"), "dff2e73091f6c05e528896c4c831b9448653dc2ff043528f6769437bc7b975c2"),
        (hex("74ba2521"), "b16aa56be3880d18cd41e68384cf1ec8c17680c45a02b1575dc1518923ae8b0e"),
        (hex("c299209682"), "f0887fe961c9cd3beab957e8222494abb969b1ce4c6557976df8b0f6d20e9166"),
        (hex("e1dc724d5621"), "eca0a060b489636225b4fa64d267dabbe44273067ac679f20820bddc6b6a90ac"),
        (hex("06e076f5a442d5"), "3fd877e27450e6bbd5d74bb82f9870c64c66e109418baa8e6bbcff355e287926"),
        (hex("5738c929c4f4ccb6"), "963bb88f27f512777aab6c8b1a02c70ec0ad651d428f870036e1917120fb48bf"),
    ]
}

/// 長度 0~130 的訊息摘要(CPython 3.11 `hashlib.sha256`)。
const BY_LENGTH: [&str; 131] = [
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "ca358758f6d27e6cf45272937977a748fd88391db679ceda7dc7bf1f005ee879",
    "140d811b81973993df99b8b1742b383ab83f6f52bf7af850812e7bba02ff11da",
    "647674a296197442f518bcca323ec605dd8d098b2d4f22ee1fdcdd2bb753a189",
    "b999f79c534a332dfb989ab78cda3d1967c16133ca1d668cf62737f8d768962f",
    "a7fed79902b79407088f9cc6584f7506f82d8be43dc419ff5ab1969dfdd245f5",
    "35a9838ae94607ca89c07791a9682ba4a7a4ac9a30f262439c067eeb64ae2ad1",
    "13ce3403bf0ec804d44556f2fce6a75e4117680fb8791e7bc631bb7ef0d13550",
    "4fb900ca3f5832fcc475b79bf07217bf0edfe9d39ea10f5cf624246ff68b47de",
    "1a4d14ade81567725e079c6fc24507fefef27d92c7ac4086d9f74b89ef2f0aa4",
    "49e62c55d4996b5c5092f67f3ca52e954e37310cd3e07a25be69d3f1b8d2fd98",
    "b1ea037a5a2028c5faa5eb98708424f1ac7896731528711341f236d8c19cabd1",
    "d255aafa782c787b223925cbe2cc9d234ee70cea08b951f0b0c0b3f8b82e0916",
    "dc898e8ef5663ef7a22697ee877b3c4d62ac6f2a60d87187432172e46ece2cc4",
    "4327538c6f8ae469aa369a03eddf44f38c784d258bbccb6e1a571adaa0ad9d9a",
    "45d2755c5c700f214e3422972d36e5de4416645f498926ac719fae09605c09f4",
    "f087c7ff57988205ab8885ecbfca8a77c96e91b213bdaba91143fbcd62997713",
    "b6ff0191041cc77b1ef514adaed53fdd247fd43221a629d3d7c91d14e21038a3",
    "df1d155105ed3fd5a96ac0cc8a03757b8b129af594f42415eb239a4b4bc75767",
    "b8c31728a52aa1d6dc0c74c313c5920752a3e4b6aee9af80355626e46425d870",
    "3d9862867e0f08fe66a4a8060f470d25cfb1dbf7705249bc343df0ae24aff0c0",
    "fbb90f5e6853482c6429452998cfd075da3a022b888fe1656fa06db254a5febd",
    "8f98945d76a89645faec1fa8423320e1c667fe187010df451ec31aef07ac92bc",
    "e37502a8c138a926769fa31e1f8897d8c8ce9869acbc47a08228c10733bd4a7f",
    "534ed31261a5fa7479dfdf11a17fd67c54f97f709c8d0cbc7d6cede4250848ab",
    "62394da95c50caab10618ac4b91e5855e5d6070fdbc4f3fcf236ad6131552a01",
    "476f63083003ae90ee93820b6424e7fe7225295d1df672f1980cda7611c7525d",
    "c254534705c4b62acfdb7559129a2fe1f4a6357f29adb2ce0272a5850ec18779",
    "44e10749b735119d2bb8a035ab9e80feaf2d70ff0cb4ff81009a7164ea7ef158",
    "635d558662aa3a0a816409012f1ebddd3e97e5b47b104ce3cf2fa51eac6ba03c",
    "96c7f171376cadae5507a4e86f209ecae9803c573b4c12ce092cd3a406753ee0",
    "5e5f9fa56d6337115e86a2508477e87c7d5296d0b0743ecfde2d0caeed2db37d",
    "8e889f10b21cdd1b3ad72f740317a827d76e1b5b3f721e33c566f06d1deff8ea",
    "93462e85c42aa037bc727a8c283497594aa5c844f0e5bccaa52fec34dee9f44e",
    "7eacf4e83a0c0f6a275d8638bb4139f57032be7f92faf18061ab123956e839f7",
    "694e74e19ea404ce1fa456cd86a21805c1d5e3b9ddddac82049e1930f7916207",
    "1b0e5a6e99cf2857502ea95e3c9fd8623a7b97c736d2a3ea51007cc8f4e4459d",
    "1de539554304369f30c38f449ff784bd5631da1c116826ad6bf9eb941b4def8a",
    "919d8c7274eabf2f23564d1ffaff05432563380a336ad020c475ede6f8e758db",
    "4673c1ba5e3149dc9378b6991aad7fc5b9eea309128de482e41a4a26b1014253",
    "0069ba1486c68c9d9b6696145417e15d575490572a589cb90295d1d646ab168d",
    "713465c48d1af54b1ec09afeb0ff1d2c1d903e2209e6456cb1bb406feadbdadd",
    "d59b89452cfe95dea27e9ceb867031c0d1b009b7a34f7f9579033de9fb1b7025",
    "cb2918c166fc0ce29240404ded678790ec1ed1081a44451b12db86af947409dd",
    "c6c2394c8738d718c9267fa44a604af1730c3eee5b49206eb0b3afe51fb9094f",
    "70e67c7bb6676134b17de565343d978e42a4d9c451081b52dd7305d39db8998f",
    "efe965a979e8ea0af2bc644f948b5d9ff44aa32c9ef16696e7c39b43bd34eccc",
    "0b87b79f91e5d8b08a2b987530d2661a815cb36051d1d9de643d9b77f8b0000d",
    "dfdea792658d9e37734453208d20a69207f91e45b38ba9fb35d6a855cbcaa859",
    "133936d4a4bdf0845e1a7f6eec060132a2b4f9ca028e891cc9d491f588cdf0a5",
    "9482749d4936c40304ce3449d92fd738a8bb2919dcfb63ef738a19e3a3b01d04",
    "ef91e8e4033ca1960bfd6b772d4992f661954e6285ec81c8abe8d89e2fc8a04a",
    "56d20b3f0c9a4610c8f555349d5a1acbd77f4ae92e3e1052562e1df97c1831bb",
    "6018db3f8d14f3b05f0ca750fd780750808dd09a23128cdd80a81251b2c5d519",
    "c802146d5788fb540fbf29d8ff485730ad10f4f13b78961c032e78691b582647",
    "8aa994584139d128848eeebc4e815639ba5ab6e6e39574195a63ac4f14f7c43b",
    "ad574708f75c044c9b85de64cb568ee7711ff4f36448c6242f053ba8f6cc2b63",
    "5b46e502092be01b1100193e089fdda95638c12e19a1d24f308eb2c3d3ae849d",
    "b077ebeb8236a3aadb7f9f3fac9bf78df7e2ae0e8ca49d19f36914c66c2421ea",
    "52c10381fbaf5149f1c9a1df701baea05f74df32b80fa073943df58b61942ca2",
    "0cd53cd7093df4c301a67b8072a805e69508d9336a4a237f760dd989994fe7a2",
    "e8e5a95dd7d96e970954472cc2ed73edca2c48c710048b858c31996ce769a382",
    "8a670c7c037c3947aa18d2a2a717c1814a210f51ea22138c5bc43d5e09f63db7",
    "280ed3e8ff1df845b2e7dfe6ac6cee817bef20e783cc65abc41b818b4d2fe076",
    "c6ab9724ade5b6a7a1edfffb12f3aa9181351355af8fd08c919952ad211339dd",
    "788367c73c7ddf4c53f65e68cc0d943e6227ab55b0e78ba63ace822b1c6301c0",
    "c24f29299c40d868cd7b1f5de6af00827b1a8454ed22256f8fad0a23651d8cb3",
    "978d3882da4335d999565e8f6a1ad3c67d05d13200305ca88b71c15a6e3e85c8",
    "73205e7093c6b53b335bcedba2eeea7ae1f9d2fa189709ff17c2c70b11c758b3",
    "0bc9554f0d0db189aedc00e391072ca3f9727d8b00b83fb701f0569066c01ffa",
    "d9c27945a73a9005b52f13594479b695ed1c4e96f764e62a19ea2e32af9b44e5",
    "b4d079c3da387729008e096f129032366ba3b1b13f07c57352afd083824d1de9",
    "9c6db2e3af616bebc9de6e90f9d1be29621157db8a0718aa2fe36d84fc451f87",
    "5289296269eaf1e62e77cff45f520309cb0a5d5ac7290e09be5714cb3602ffb2",
    "7bf9fa01704b1fc3a5aedb1cc286a29f2c1f380a6ef5c50f6e208e8e86b34b01",
    "56b127f7acfa1c21854e9f44179d88588f68be841c0dbaba735f65f94ba42207",
    "c30425ba5122022f0cb5f2b85fddfff45900e6e7f3bc4a064fa5bb771a3fc288",
    "2b5636493c358e72d577ce8afbeb79e6757c188a6fd31d029a2821e85542833b",
    "677f8cc982301dc75b9b988799379e69b7b676b117d886466692081cb33c0bc3",
    "9f419c1f641a930b61e7861f11c3dd716bc05991a072efb8b5bbef7436d19aa2",
    "b26499a826b8b46533c2d582cd456c0c3ef988e52c4f2af931b44baaf18de26c",
    "2cbbfa85a5052ffdf904ef426414e11201bf118874ace71e173822b1f037d38b",
    "5ee37ccff83bb4c9590fbb0ed39bbf48cabcbc5af621793c3bde3140c24e805d",
    "d148a9b0b08ada1301de8deaf952ed6d9ef8722928d57d2dc6104ed4eb3a1a47",
    "a73b761738961969a2dd7b875bdd38fcd0b1abea466bda5872f5bf1de8511d84",
    "210e074eadded47ed7cb727d9748bd14cafda623bc6947cc1e6302688da64d22",
    "a305fec92849b9c0765508d5010aff39ff3126e4ff4e10072199d8c02ce8defe",
    "ad5d8afb4c5cebec996abb96f7e9a1341c4b30db6d105dbb081d98454cfc7a6b",
    "efec7be79a6481cece434f8b463bc7800d10b8208c93b4fcc8c71f7f6701f71e",
    "61208160d58e9662571cda06cb9714095edf50393caee99cb75c5a26f488e19b",
    "2510bce9f3bed86186f4a98def953dad75bb13a89356426603a22f81c9ea5768",
    "25430c9c296d09c08f26d7efd74da726e8b705275e7a212a1fbf9d611c54c84f",
    "087ae647d38e728654b8d64960b8b59f31e46f98186f36e2e6a87e9ff6a6418d",
    "faced2ae498e7cce764c5f3c6a59610ec089b9d8ab67a2f81c1fca0b4403934e",
    "9d362998ae54695e7f832df638a353822f1283a49118f908703fdb0803509424",
    "d8c1f906be7970fa1b45890c5b45f94564ce77dd02bdb8cf0b869ca0afaa89e5",
    "d6c2773235f3785b4cf0f2b11861675cfd7f2a033ae69df9009cce1a787188b7",
    "15218c39030268e3258c9d4a8d3f284885a3784d0a0eb64d0e3ff99630457198",
    "c0744ab5ad80d3d6b460729c98230900a31d8c458ea5b462c79ed8a255e230a4",
    "717c23feab1f6a3a42269fc90b88799a1634028b67423f14b8602498bb952546",
    "c22e490daa445fb2fba44278c022df135310fd278cabca4ad7919eddcccd1dce",
    "e074ec684ae30cd662349906698baedc326789d9048ddd3dc1d43c6fcd5ab215",
    "52b5aad34021e9763bd2f719103edc8792bbf1250064eaaeab3b618cb31f1605",
    "e571c2a147c1eceec5dd8b6aeb1889aae50ea41b623d3026abc665acff5201f9",
    "23741790d156ecb2e1f43040faf528c96695945ff60b140c017b18137ca88888",
    "b9eeabc1150408b0798f41474ca2631a1e1d21d596db4420f3e23a2e05b3d9e3",
    "5d996879165390ac46419c0d499872248af518d37f368517d601e9404dd4e543",
    "49b9f17a7f3c6d94ae8b82ae9f94f367750c2c96b5b3e512c0c70cc818caf741",
    "189fbfd57dd81e95f3328c00adf69cc226c6c6081b21dc12f60ee1505d8d966c",
    "28ff771381251bcd442093a809a61095f53d6b83b7df9e59d142570bfc2a2835",
    "fe47c5a8d830476f3857f334a8a6d25f51270b9ab6f5d6dfaf5cb87b57c7aa91",
    "dd1413178fb627f9abbc041ffe39c44aa7aaa0e2e6d2ca5c4528ac7073a2da45",
    "a65c92dac124062d0ab951a42773cb04fc98d1d4bf8897b176f8cff3509d379e",
    "6f184b6619128e865ecb2b3ea96c03d461f0664d87689480988dbee53a449161",
    "81a8edf98294aab58cd1624aa4eca96e7f12de7de41005d08a5dc160a3c66ed6",
    "acb4c84cb17d887b3411a138a357b52be28f487418f65a0c5dc3b11a1337ec6f",
    "0d6a9d84e67cb35fb772c46763b46b72e229b5f76663c5ed53343424775ad100",
    "af7b162f08dae5e87b4008e21010c646a576e3372d6814edd32f9d01949deca9",
    "e96230c1485dd2e36f02f30932b0e2acf725283090cfdd8c58fce6bf523edd26",
    "3d610547d68216dedf7435a4fb6260353911f6b3fd3f18805ddb8be285d726fe",
    "1f80156a804cb7862ad113e8200e9d74499723e7c7854d5f48776d3148e09656",
    "614571410beab3df68d50132a341d338575653da8374c630441bbe380b9b3136",
    "d728ad2179214f0cb2b01d264d8b7fb26893e310599d5d419c7a9f92ca293664",
    "893ef3f88cf4382f5d660f694b6b4213960adffa842aca38988fcbc7ea5b58dc",
    "2a4c07c863a78da189481963834179ff348b51630ccf098b923d871ff748403d",
    "5ef8fed0986749855f87f2125e13fe813d9c1428ef0ef36bf49395b53d14c85a",
    "5aa67f561ca036a72db939b4d4b14975505f08fc1564036822a1639a5b09dfb8",
    "192409cd280e14b743642ad1343fbd3e82d9305de72c078117745a679210cc3d",
    "cc548ca2dec1f6fe4f58b2e27aa9c7521607df1130d140b55a4dad0665302356",
    "81e89a7b2911aaa7795f9e3d4910cb47d6cd2b00d83b8399481527261a1a7519",
    "1c7c3b5eee94d4fa8b41754b89153e50491838d0d3e49b0273d6f12cae12e387",
];

fn message(n: usize) -> Vec<u8> {
    (0..n).map(|i| ((i * 31 + 7) % 256) as u8).collect()
}

#[test]
fn nist_test_vectors() {
    let v = nist_vectors();
    for (msg, want) in &v {
        assert_eq!(hex_digest(msg), *want, "長度 {}", msg.len());
    }
    println!("NIST 測試向量:{} 筆", v.len());
}

#[test]
fn every_length_from_0_to_130_bytes() {
    for (n, want) in BY_LENGTH.iter().enumerate() {
        assert_eq!(hex_digest(&message(n)), *want, "長度 {n}");
    }
}

#[test]
fn incremental_updates_equal_one_shot() {
    let msg = message(300);
    for n in [0, 1, 55, 56, 63, 64, 65, 119, 120, 128, 129, 300] {
        let whole = digest(&msg[..n]);
        for step in [1, 3, 7, 31, 32, 63, 64, 65, 200] {
            let mut h = Sha256::new();
            for chunk in msg[..n].chunks(step) {
                h.update(chunk);
            }
            h.update(&[]);
            assert_eq!(h.finalize(), whole, "長度 {n} 每次 {step} 位元組");
        }
    }
    // 一百萬個 a 以不對齊的分段輸入
    let mut h = Sha256::default();
    let block = [b'a'; 1000];
    for i in 0..1000 {
        h.update(&block[..(i % 7) + 993]);
        h.update(&block[..1000 - ((i % 7) + 993)]);
    }
    let hex: String = h.finalize().iter().map(|b| format!("{b:02x}")).collect();
    assert_eq!(
        hex,
        "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
    );
}

/// 整數 n 次方根的下取整。
fn iroot(x: u128, n: u32) -> u128 {
    let (mut lo, mut hi) = (0u128, 1u128 << (128 / n + 1));
    while lo < hi {
        let mid = (lo + hi).div_ceil(2);
        if mid.checked_pow(n).is_some_and(|p| p <= x) {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }
    lo
}

fn primes(count: usize) -> Vec<u128> {
    let mut out = Vec::new();
    let mut k = 2u128;
    while out.len() < count {
        if (2..k).take_while(|d| d * d <= k).all(|d| k % d != 0) {
            out.push(k);
        }
        k += 1;
    }
    out
}

#[test]
fn constants_are_fractional_roots_of_primes() {
    // K[t] = floor(frac(cbrt(p)) × 2³²) = floor(cbrt(p × 2⁹⁶)) mod 2³²
    let k: Vec<u32> = primes(64)
        .iter()
        .map(|p| (iroot(p << 96, 3) & 0xffff_ffff) as u32)
        .collect();
    assert_eq!(k, round_constants());
    // H0[i] = floor(frac(sqrt(p)) × 2³²) = floor(sqrt(p × 2⁶⁴)) mod 2³²
    let h: Vec<u32> = primes(8)
        .iter()
        .map(|p| (iroot(p << 64, 2) & 0xffff_ffff) as u32)
        .collect();
    assert_eq!(h, initial_hash());
}
