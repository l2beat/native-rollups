// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "./InboxTestBase.sol";

/// @notice Exposes `_extractAccountState` so the proof-only cost can be
///         measured without a queued entry or signature recovery.
contract ExposedInbox is ForcedInboxValidated {
    constructor(uint256 maxQueueSize, address rollup)
        ForcedInboxValidated(maxQueueSize, rollup)
    {}

    function extractAccountState(
        address sender,
        bytes32 stateRoot,
        bytes[] calldata accountProof
    ) external pure returns (uint64, uint256) {
        return _extractAccountState(sender, stateRoot, accountProof);
    }
}

/// @notice Gas-cost estimates for `add` and `prune` against vitalik's real
///         9-node mainnet account proof. We can't sign as vitalik, so the
///         strategy is:
///           1. Measure the execution cost of `add` / `prune` with a synthetic
///              single-leaf proof using `gasleft()` deltas.
///           2. Measure `_extractAccountState` separately under both the
///              single-leaf proof and vitalik's 9-deep mainnet proof.
///           3. The proof-depth delta on `_extractAccountState` is the only
///              path-dependent component; add it to step 1 to get the cost
///              of the same `add`/`prune` call with a realistic mainnet proof.
///           4. Add L1 intrinsic + calldata gas to get the user-facing cost.
contract GasEstimateTest is InboxTestBase {
    ExposedInbox exposed;

    // Vitalik fixtures: two proofs against vitalik's account at recent
    // mainnet and Base blocks. Both are depth 9 with similar shape, so we
    // can A/B them under the same execution-cost methodology.
    address constant VITALIK = 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045;
    // L1 mainnet block 0x1814849. State root from `eth_getBlockByNumber`.
    bytes32 constant L1_STATE_ROOT =
        0xdd27ed6acfed865a1b5c7d20416067d320f6e91ce3d370c2668dcbf2c63393c6;
    // Base block 0x2ceebfa.
    bytes32 constant BASE_STATE_ROOT =
        0xd7fe2112ab8a3051bc42f66afbb4fa50075a219361057546381f89010ebc2bdb;

    function setUp() public override {
        super.setUp();
        exposed = new ExposedInbox(256, address(mockRollup));
    }

    /// Step 1+2+3 for `add`, plus L1 envelope. Prints the breakdown.
    function test_gasEstimate_AddAndPrune() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        // --- single-leaf add measurement (full path) ---
        (uint256 l2Block, bytes[] memory leafProof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;

        uint256 g0 = gasleft();
        inbox.add(t, leafProof, l2Block);
        uint256 addExecLeaf = g0 - gasleft();

        // --- single-leaf extract measurement (proof-only) ---
        bytes32 leafRoot = mockRollup.stateRootHistory(l2Block);
        g0 = gasleft();
        exposed.extractAccountState(signer, leafRoot, leafProof);
        uint256 extractExecLeaf = g0 - gasleft();

        // --- vitalik mainnet 9-deep extract measurement (proof-only) ---
        bytes[] memory l1Proof = _vitalikL1Proof();
        g0 = gasleft();
        exposed.extractAccountState(VITALIK, L1_STATE_ROOT, l1Proof);
        uint256 extractExecL1 = g0 - gasleft();

        // --- vitalik base 9-deep extract measurement (proof-only) ---
        bytes[] memory baseProof = _vitalikBaseProof();
        g0 = gasleft();
        exposed.extractAccountState(VITALIK, BASE_STATE_ROOT, baseProof);
        uint256 extractExecBase = g0 - gasleft();

        uint256 proofDeltaL1 = extractExecL1 - extractExecLeaf;
        uint256 proofDeltaBase = extractExecBase - extractExecLeaf;
        uint256 addExecL1 = addExecLeaf + proofDeltaL1;
        uint256 addExecBase = addExecLeaf + proofDeltaBase;

        // --- prune measurement: need a queued entry, then measure prune ---
        // Re-prime: bump the rollup head and republish a stale-nonce root.
        l2BlockCounter += 1;
        uint256 pruneL2Block = l2BlockCounter;
        (bytes32 pruneRoot, bytes[] memory pruneLeafProof) =
            _buildAccountProof(signer, 6 /* stale */, 10 ether, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(pruneL2Block, pruneRoot);

        g0 = gasleft();
        inbox.prune(signer, pruneLeafProof, pruneL2Block);
        uint256 pruneExecLeaf = g0 - gasleft();
        uint256 pruneExecL1 = pruneExecLeaf + proofDeltaL1;
        uint256 pruneExecBase = pruneExecLeaf + proofDeltaBase;

        // --- envelope: 21000 + calldata cost (16/nonzero, 4/zero) ---
        bytes memory addCallL1 =
            abi.encodeCall(ForcedInboxValidated.add, (t, l1Proof, l2Block));
        bytes memory addCallBase =
            abi.encodeCall(ForcedInboxValidated.add, (t, baseProof, l2Block));
        bytes memory pruneCallL1 =
            abi.encodeCall(ForcedInboxValidated.prune, (signer, l1Proof, pruneL2Block));
        bytes memory pruneCallBase =
            abi.encodeCall(ForcedInboxValidated.prune, (signer, baseProof, pruneL2Block));
        uint256 addCallL1Gas = _calldataGas(addCallL1);
        uint256 addCallBaseGas = _calldataGas(addCallBase);
        uint256 pruneCallL1Gas = _calldataGas(pruneCallL1);
        uint256 pruneCallBaseGas = _calldataGas(pruneCallBase);

        emit log("=== L1 mainnet (vitalik, block 0x1814849) ===");
        emit log_named_uint("[extract ] execution", extractExecL1);
        emit log_named_uint("[ add ] execution", addExecL1);
        emit log_named_uint("[ add ] calldata bytes", addCallL1.length);
        emit log_named_uint("[ add ] calldata gas", addCallL1Gas);
        emit log_named_uint("[ add ] TOTAL user gas", 21000 + addCallL1Gas + addExecL1);
        emit log_named_uint("[prune ] execution", pruneExecL1);
        emit log_named_uint("[prune ] calldata bytes", pruneCallL1.length);
        emit log_named_uint("[prune ] calldata gas", pruneCallL1Gas);
        emit log_named_uint("[prune ] TOTAL user gas", 21000 + pruneCallL1Gas + pruneExecL1);
        emit log("=== Base (vitalik, block 0x2ceebfa) ===");
        emit log_named_uint("[extract ] execution", extractExecBase);
        emit log_named_uint("[ add ] execution", addExecBase);
        emit log_named_uint("[ add ] calldata bytes", addCallBase.length);
        emit log_named_uint("[ add ] calldata gas", addCallBaseGas);
        emit log_named_uint("[ add ] TOTAL user gas", 21000 + addCallBaseGas + addExecBase);
        emit log_named_uint("[prune ] execution", pruneExecBase);
        emit log_named_uint("[prune ] calldata bytes", pruneCallBase.length);
        emit log_named_uint("[prune ] calldata gas", pruneCallBaseGas);
        emit log_named_uint("[prune ] TOTAL user gas", 21000 + pruneCallBaseGas + pruneExecBase);
        emit log("=== Common ===");
        emit log_named_uint("[ add ] execution (single-leaf control)", addExecLeaf);
        emit log_named_uint("[extract] execution (single-leaf control)", extractExecLeaf);
        emit log_named_uint("[prune ] execution (single-leaf control)", pruneExecLeaf);
    }

    /// EIP-2028 calldata: 16 gas per non-zero byte, 4 per zero byte.
    function _calldataGas(bytes memory data) internal pure returns (uint256 gas) {
        for (uint256 i; i < data.length; ++i) {
            gas += (data[i] == 0) ? 4 : 16;
        }
    }

    /// Vitalik's 9-node mainnet account proof at L1 block 0x1814849.
    function _vitalikL1Proof() internal pure returns (bytes[] memory proof) {
        proof = new bytes[](9);
        proof[0] = hex"f90211a06ad9e97fedfdc61460aa34618db38070a7cbe25e9044c01b19ec40fd9894faaba0ba2a56a96a04fe43c7c150e3abb6859ae765101d61b94d352e80c4d98aa7cd6aa07d11088ea9370ef8b993b242b0cbe37cdef9a0f90b1f1fe3ae291e3037e11a04a0b65290ffa164726373ac432887b0757131340c81e011827cffe084fe037daf6ea005c996e43f8eea136cd33dec9242fc54a3c719d27e5ee3a44ee19cb5327b4ed8a0c82b3e3cfac8a08d64036ce901750b2289cbbe049dfed1c007ad581881156f82a02dd593aa8ca8056a19d5af5fec408fc700cca3a095dd088902bd40c8449c1796a0c610f106993a9594b97d895d03a7de41eefabbdb6a9bb48099393d26c7a8546ba01d2062af9012492a63173dddf477b6b1591fff0760a744a17b27e8994df49e14a02dc3ae9c53484f4173234469f190542c7471cf53e9d4d87fdaeda0d0257de8e9a0ffbf4b0c9cfcc5f56a317fa88b0bcc26580b2c16f6db239c9d31145501cc35b8a0d66cd5641980cecb5ae0a5ef35189f9b1a97d83dbaa2af0c252552c5eacf3caba0ff7555c6f0ceb964e1dc81b3e891c6531ac1fdc2ef97e34b82a6bdffe025c69da09e512fb345ced129449ec4eb2a6c771997eb1c13611b4c6050b80b070291dd69a0a1d1852c1a7a8e16b066bca1048fde317117c4537f76075d9e7ef0b15a75ed77a0da95342ab9ab7752fa9315005e2c2e3c06fa6faeb3e90aef2b06f515a3427f2980";
        proof[1] = hex"f90211a0c8690241f097947acd561e3e7d7c5d7eb7d1c4f673f66016c966df534317f5eea018b28a44da35c98518fd7aea0f6ceeaf47a957db9cffc1f8f320ada4e280e0c7a0b650a6f789af0e36f38f818c9be446df5b2c3ca88bce9e2bd89decfd5a801f29a0a0a83835b1361831d658db53a1067f6f9f969dbad5b89b5098e2d9ca3ad075f5a083db40d1e2f7176992e3f81103159205d7473b5f366478902398d89b862bc9d5a0e10d3e2d60457c1eb5b02506345d4126330dcdf7ddcb3bed6648e25a4e88e9eba0b3451b29e6c9138f70e8f6894c8d49c22f205239b38f1c226307a6b142c28759a06df0658308583875dcd61c12e523908f8e3db739eb318e1b78279d8083d43333a0aa1d730d8d0243e78bb71ec31d571564e52fcfe9c5de9b4702b3900566fd7b0ba06be957f09400df379f36d209b15ba153348d73020c4737d17e6cdfd093c099bda072a6fe960eedf8a3d8acbbf79c7085166e313711ff7ff2a2e70528ad0d5a491ea0286abaa9be3cc58d0601e14036cf3e6fe518dda37c75ffec37b33d2fb800ddc1a08a495f4afd38fa245905aefd14c25420abd6d6bea0fee51340ab168083be6162a074657709b04106f011077c16b6c949578224c519917a7aaf751017033459ddeea01195a1ecf8b1d10ee71d7a0a9e96f3a30d0d489e207af762a2399dff7f568670a09d2ef63b206e22830daca572518de3cdfd9bffa4efcf0b8bc328aaf83b00403e80";
        proof[2] = hex"f90211a0508a950c52f2a9f3944e8b989377e0486383633ccfb56a6d29adcf76a6fda27ca0394ce3a4bfa1c2644ad7f96db5ccb7af484ac7258b5ccdf1144b0f8c1cbbc978a06f747e6c7c69b1f59edd849d9a64142047745333e8809f5c77c01bc93818b2cba03931e050a816ea9f00bdcc83bb30866d32bed1378a45e12329a7d2ff48696f55a0c47cbfb1de802ce57bddc4070189f63bab7cd3198d7dcb3f93c31a7305900affa08698cc52d8564e64b9b89f16635a138498dcebb173b4aa3975e14f671df04cf1a083d9f27296e946be3adf1dfb4a1984f0b9778cc84218e28eb3965af624a1ea30a006a663ff045ad30ca1f23ce4d9372eba82ded9a5bed27725361709e0a4145386a06e4c743e772ff61747b913465c8c224e7586f977eaceafcce3cf2a54edb80f95a0cb0f9795082d04fa2afed597a50bba91f801dfd1236bc4e37abfd0db7f92e40fa00fd6ff12deef2b0dee4507dc4395e67f43f875c4eb9a46636dc6c46be9ea68d4a0d747777f5f3d8049db440bacd184ff155892658897410b7137876c2db6518476a0e9365f376fecece7a74e2453bc0a204dffb6444e723725f875fb643c428f5f5aa017bfc1164008274feed1b4b19001eab8b52986c181a4b138a365c7c5c4a6db6ba0c1b58783c4fa2299e3606c66d8fe41b1cdaa4169e1ddc4f602caaa743e4b3899a003642e3e62b2643411fae1fe81eb126f20ea3dcb5374b4ce7ed0699c9bd4539980";
        proof[3] = hex"f90211a08fd9c0e7aeb79c9484e3370503ba4c4e215c2f2fd55455a2013db29d559c2e00a031dfc9e74e5abb59135aedda71f851e836c5664e0ff1f71ada85c1fd053ed6c6a030d1783800d7fe22b580c9674a277319104938cefc4a52d9c73d903f4b964499a0661f4bc907884db171de45801a91ffd9ac7049881a9e29d3915d59143ebd118ca07d4272415df9d44e81c4fe128194914d372d4d8eb80429c54a61a59cbe70c210a0bf3d600724e7172a92cbc2db04c53d524b12c959cdf1d27c7a976bd96321bb20a0847341ae0dd49f5fdda4143b0552c0a1d0c1802323e569f483fde8733eecbfcea0af0953dab1bd08e2f3269dd7ef8795787370b883b33eab86fea9f7ec2ca5db37a0d7c7e39ee9ff10ff8ae190bc6e5b1da8a104d965e03e3bde4a49f067dc276481a027a84b87e5913ab9032328fe88c09c80bbd1999680028e67a98f8c5949d0cd89a0caa66e4433c4bff9b845095673f5631d72fab02198e49715692da6644b860d2ca06b3d9afe4ab951021afc74f7301910086638f19db950e557c9e85f3e80fd4098a0a8c2c59a43e79d7f6a03c5c289a0fe1132a5200c34090c15b0a7230600cab2f9a063b2593305feb3925aaca8ed39ee7bd8be27213da964b35e20bcc9f2c9b896c6a0194f64aa1e4a734dfff28e8dc1d70c28b4ce0dcf21db2db061c903592286c009a05f551bee3fa307b9dec0a63ae4506689a0469ecb3448100e2148e948350ed55d80";
        proof[4] = hex"f90211a06ad5d5f7b4d63c171519fc9f445c77542e096968d02c55459a551f0eece74921a0d9fac1de3ae5e45ce73621e0b06f382688a2ecb30493a12287631172aa05ec50a05695b95c290c2948c51d5b76f1bb4337b274e7d5e6ba885c364971debde98e78a0325a17e5f196200e3a4199550af05f9bf5db514b99222e759a97da8c46e97506a0794b46d7ae4e6f6cf6fd9872706423a05c68af721d050a71ddd2fc9f12c8b938a009428f18155a1ea8fd0bbcc8725824437a3a2806aca0da0783960d401efcbe71a01773ee3c10b939baa23da4d0aa5f575b4dd395fb2d137b081fad86c09f6c9bc5a032ee4a436f97c94916b48b7731adf6d431e9305a25a2bc202a9d70903d7b039ba0ec0120479077b5d76638598e7b6ea588179fd5d2383fc9a561296c43cae867cba0bbcf34261f882e6ccb6262afc2fe971760961c454f983e1d66950361bef04129a03258a751f4aa36a1a285b31e9845b4cb5465797d6446b2f1f6a910e139f95befa0a3a80cdc522ffa007947082f4fa0e4d3d9451f1aab230d1e6b2a2923a76dcabca044dd97ff774d9dd0438dd599e23cc321247be7480809660beb6474a973c55bc1a02ea14571de677ec024709e2630e0ec5e38e29b9b1f39e052593ed9f528187568a07b1a3b5175f71ebb696375d73bc60d29a109299b18abdf97a92b360189aa220ca0845ee9a39b1b5b31505f66f2a58eb9d797268eca128749384066ffe32377a01a80";
        proof[5] = hex"f90211a0068677306f608752b080f8885c1f03af88c9aded1b3dbd4184bf9245c414ddb2a01af7f1f0748d2290b5abeeb73522d43d9e8e863f5b4498a4f3a8cc36393d4530a0b3ad8f059849f4ba7b6c4a565f0731a5de0c4f977c2eac0cdc1799645face76ba04a76a6ed72b59e1a26d9b87cbc9d5292b544508072f15901ecbc4e1e942baa4ba0c91a7ab72f3c65f908c33ba352b9948ed995b60ce0148d55e84d61dcb46c052ca008b36ba7771e910f5107ff917a144d7bc621ed4a31b47fd6a77073bca51a0f5ca0cc2da910041b3d836b7432ba06c18ec3a9fd4cb3657a5e858b7a4f2972bcbb5da0eaff5413208bf05210a888de66a89c5a5602109cecb0e72e56f2728f22a626aca0bb40cae8add812a5303f9a4a2f4ea279b85d554ea76f86e59e29a473d1b19612a070bde8ee98293768c974de000311f0a7049760e946b046f38e510ac6b8907fbfa0dcc531122a2d4b8bd224e418a2fa7a56008558281141f1413110d5ad9a6d5d1ca07b043a9bce9f3946b48ab632dc1aa11153fc748ded0e5a5faf10c77acbb1fa58a00e758459ee35e5aaaadee502f210a9003453e05b42ed87a33afa1a5c31c75622a062f55bca34eeed589069de4f8cda6907992645959789f13a98be09c3a2546f5ba08a35bac8d5c1480c7bbacb29ca02fb80b9e62cf30e5fbc8f88b51d3762c636eba03a94210ba0b20e4ce22b48faf70aaeb801c40e17596c26674869c974697cadb580";
        proof[6] = hex"f9015180a0b4e251151ceb76bcce749928e74ed1f9a4ea7c3e6adbf8938fecc9deb8b8832880a024b083307e38f2a989d3be587d822c36a22089057cffad0ec159b4183ae46b4aa046640e820afa78cf8f696081f6b9ff0ba4b7574d9b5b62e6d2e1560483dd7ad1a0ff7daffcfa68c12ae52a590c6317440edefc0e2df4b58c1f64c45dfc10bc5f57a095cfae180f39b2ccc507348a4ebf3d7f38bfd789ad103e6c053f4de0debfe09ba01bb319c641298631cc8b761b822c79f3e7faecd8a44b9757a7fed1d36bcbf45280a0719325223ec4156993e85c24c5e4f3b2c9ab92d2ec2ef4fa193b1e6b89a7e1ed8080a023dbfefac222f32aedc8dc470b38495946fbb810f36fa322699983061ff8e278a089e444bd8ac255f3e38f7475cffff80c3d3892963416d2d7dcd9e9231339400e80a05dfc3a0fc6cf77a77df1f81deab009889690fd7f3acc5fe5f5f28fcba4907a4480";
        proof[7] = hex"f8518080a084719fd87969f70c010ea551db9ca8b81ebe58735b8a18fddd932d38b9b98c7e808080808080a0550a2773f169ae53acf443a62e00b73865abb5bceb6729df4012b9b9370eac3080808080808080";
        proof[8] = hex"f8709d20c3547c60ee47f712d32e5acf38b35d1cc62e23b055a69bb88284c281b850f84e821708884edd4b61727a96a6a056e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421a0d8ef78646344da0ceb69cbcdb306939b3ba0514174fa28b6c6fa189953ff226d";
    }

    /// Vitalik's 9-node Base account proof at Base block 0x2ceebfa.
    /// Captured against https://rpc.l2beat.com/base.
    function _vitalikBaseProof() internal pure returns (bytes[] memory proof) {
        proof = new bytes[](9);
        proof[0] = hex"f90211a08364ded467701ef67d3b9217e84617dcd446b809a41f2f224e94c391828c01fda0024e3ffff4a71ebb5ae0b6ed5babc3df91716410c2da63d84622c1677e195022a0ac2cfd4e61bd2f81c134d98087f1562037a2b68ff8453fecad60a8a7acfc4eb7a0d68f28937bbe6fdd612c46bc4c59c4c200755c5e5362dce9a0880bb4bd029903a09ee972213b84f41cb5bfae6cc7883979c1b7638abfee5eb341b66ac29d9e78c4a035044de39ba92db6fd282605f3dc47e6c20b18cae51caaf2af3a1a135b0235e3a07ebd8526a69eb1857c520f29d51fabeb1dfaba122c11edff1a5c6561e79451eca0c2b12191e7e60c61cd8baf8792b85dd6451a6c688f7e1d2eb77013d36561a043a005bf576cc9c41ccee12ad02862a404413f676510548d4f054381a2087972d521a025f746c7cba178cc0a260ecfdb4bf9cfc4d71624c6b72ad1ee6ba8443f2a66c4a077bc27c9c042bf0bc691220deb437bdc901a8c611ad469a41b6c886bf7ae93a5a0ece84bde71115cc3ad2c65c3eb9e22aa2f8eeed43bcad8e59af1583f89d1376aa073f07bef1e7909edf6ea6c8cc0df910a934b60470fb4dc9fc8e49bee03425d72a01df58ab4f38304526a588fba77cfd298a60e9c985fdb71c2df4ae9b1ade1dd62a0b84aaa2d726c26720eadf1577c20757f517075a53bc5bd9664af935582329a26a0eefd2d87d094f313141b9df6848b896520a0ff7da95c15b0cb9151931b30609680";
        proof[1] = hex"f90211a078a6064a408fb580a77f47c9298e223b9dd0b022437511e2d12eb8f04f117f86a0f39735f74f886204993653a36a4865ae73c08aedf52b37586dc3ce3aeafd78e0a003cb4ed812712bed54ff03355d4f2c21d84bdbe70fd2f9b26a18adf8ad972e04a00f7ae654a78ac3e2a65e521460202e3ac4332bac22d326fd8a990681224db290a04927ef115120f742d341c422ac0216a7ca4c8745328950b7cc0d47e3f93f63b8a0d21108321cae41017c73c15751a73e50ccc2c1b61f846c9ac8869746c76f61b7a0472ea872153f43d47b5ceb4dd3eefa1ce592d370bbf67a07db42ca5c5671adc1a05a65f71be934afa66227f5c26766905c144fdd5667e5672d2d540f0f7b0f5dfca0a1474a9f58d95ecd93afb0f9c61ab2aa921d6d54e831607a992d764dd7980990a0a318fb7d039b82cb212873d8f5af9bad57d7e67fd11d2ed97ea5a8ac492c9012a0ac8eb12e46f4098e5c51205a0c83e49e2876ded1c727eec34ef744992c31337da08fecba534706506ce2f5fda52429d55aa5e543d6ec48572d8cddd36b1a9cd8fba0cea604ab6fae0163f3460a0e6dc99ffe0d0a697561a7bea9a89e26ba14f4274ba04007d25529acb40045c134910d65d43dc03c378a12a4e4ee7f92acfca8039057a071d067ef522c65d99b58fc4f9cf450d033e492edf5a1e4228d97d05df70bf5e6a05e0f674262ba9696088744d7f8a3dd9e52cc3686a8ddd830d0bf7e5671b41e2c80";
        proof[2] = hex"f90211a0d216ebdf21838583aafd3592a79e5734aee6862c635cec9621f8f32e58d8d9d9a0f9afac3f851ec8ede0966db5bcab56e34e1d36046de55aef50d4bf387931ad43a0062aa9d241b20a80e3da413b04e777992bea9e233c81fe0ae463b2f7a2c4dc2ca0148e701d22786af10ae771ba20ae59f52cd155adae7a17055cd09237dac2ace6a06691aafbe41153bf4b3a491bb00363527c6aef47eba9b9c3a778b9d4934165e1a0e93482d176623e4b0888714e6ddf3c3554b065edef0ce8355c7443af27a8d5d8a0d456e49ee9381bf50652aa78bceaf8c0eb85549d995505b3d73054f6ae0f97eda0b4d67048de258f9791ba73a0f8e62e00a91302d2719056c1a0d60f5c531c2153a0fdb112d94d60ca411407e95119b8224856bd3a9163e82501fbe2fbddaf3bcf96a0f965b4d37558fa17de2fe61bb198a1bfaefcb6af8b22922a15d0f3974c103804a045a813d05c1a1ca82d1440543ce17d058a02e9483cdbabbdc3d1fa228d815aafa062ab4ce7ab6d5d6a4909a7c50b1ef86a7aebb42ab88fbe0686e7b2401fcb0644a0d44986f89d29a5f28787842186247051d577ba6319fe9a8c12e0453f18f86c49a09a73d0aed7b57d8142b39115dfc7de9b37c319a54f9a1e7c743eee9e98c51d2aa00fa64904a095d7972818563f11298fd3eb3c2125447e5e884bab5672281e4444a0e55e7aab4d5a4ac61f69cb0021db700f56a489534efcda9b6ece20dcd8de159880";
        proof[3] = hex"f90211a0bf11812f80f36c108738312be0168979e13e0d2edda737528378614a415d727aa01a367ac3dfd03d002ffcdd04cfd2348d116f1ad6d50ed59b4570832610f20bb5a006f96cc843d086513833c6cf0905c182175fd01ac734cdaf4b58397f3a41e65fa0b0db70aea5f928d9b89413e22f40d6b90019380dd1955a05d100272de0dcfcd4a0080d4f1c26bfa994480261b0f400a39c89a3ad2bda5d958bd804181531ecbefaa05afb88d1bbaaaff23fe7f2f547b55f5bb46959ab4d9bfe4f43a03e5d5311b0dba0159ba10e0327b484890859fda8ecc2ebffc2ac1f4aee67770dbf16bc8cf03023a0c9ee76fa7898fb99d7301c933f7cfad020c618968ce14b48e139ab3bd14d8a61a00cf0c5affec808ac9a107d46b347a2667eebbf3b01106de4e813094dbd374d0ca082eaac48d0391c51deea9cd1d8c3422685e36bf0659dc81ab18f76674b5112a2a017cc0b5db2cb8127bc149c4a919b9b530e380bc5992520adfdb69da0429bd2eba0521df68b4485b5250f8375d6cf380bcf87b6ab2010093b2525484fdf1cf2ec04a0a77b2097864d045ae9842cd78158c633bdc48cbf21bc7b18720ae4b1f93a8472a06e2aefd7073a5ebaef0c6c5e323e302e6479729479b7fc3aaab4f394d534f099a00e0cd5d069677ebad1d4e47f55619bde1676ddceeb53d29b0dfb354e18ad6dbda0effce2d5dd2355427cfc1f016a3fda2b02fceebfa26a8b51a560a587eb68e8b280";
        proof[4] = hex"f90211a0613f833b9b76151962f6b95b1ceafa70c233b5b6970dcaacd709510e2b3dbf5fa0990fbcce68e413b0890e1db44fb5b68da6342bd9a9a31e880ab7d2a86a969c15a00c62f83f481b513eee67dc6c0594f2a0c8c56d6ef1b2546e07b36f94dd20dd62a0789f6ce691774b5a26d4e9439d6e93725f1de439c42e9580e85f09ae3df959f8a05689ad22aa8bd00e8ecb8a85e6b8203782255d17534655684c585d0dffdc5688a051ff0b81494dd62e5235e39f1e58306b0235672727576c9d786bd044c1af0965a07a73192078c43dfa900dd8b070ae79ccd8f10033c841bae74c9f6e82813fb5faa03f9cd8ee4a159af08606c36afd654cecbfe32749924af918c4ed8f9973f58517a01af97c110dcb1410a685caafa852c533666a93a7d0776852f700398b4d5cd394a0866fe1da6cb737f427bc07523ee4b6f67cfd6be96221e65003581ae416dd72a8a05a3612ff3b5971d7ea14a5a1e7b5321940490f00ffab98275a3eda4b6a7d295ea054cd1d6d345122cd9c30c68dd21a29251daa8cd7793e948e4f4eaf78d096cc46a00605fd0cca867852274de67d8d2ec24dd2a087e745857c5da88aa33f0be29db8a04f08c4773abe32936ab87d9bdafb9eedc94272d22caffe08bc5959c031deb9cba067926ef5dde5b485f5ac4b20095c3587dea3b8c64b250efa011fb11b8d2ff93ca071b565c1bf86b5029eaa9e2448a0c6f89947a5fe73747ac48b2e63be222d719480";
        proof[5] = hex"f90211a04ff64ad71fd854fe8b4533d49f587580b1420dd7fb7a4e43d732411d7ca30593a0d7bacb5f83dc2879dd31217588403eb9ffaba15fc4d3729ee790efbda21fbfd9a05596d7d06209885f6bc638f458767e38e5b1d7406499bdcdfe57b12363759be5a08227ee5ec85225432890c241774988b6921d7a812b9898d53035f56d762d2d5ea0416a4baf9d8508cfc12f6190b26f7d8db6a110d2d8609cada92550bcda1870cca043fd3e80f2fceeb540de57a808a4053bbd28ecab6cd08615eb3546c38fa50b3ba05b54c1d1efefab47cb028fb50b4a9d37846171d3cba4f6fe6a13905e62874c39a0906b07cb888c09690680ce2af2070e1b8b9f0ab5fe7fb61fa365af7bda164784a0aa553e7090b240baa1e4b45df4c9c107b0f621fec5f177aad2ba7fd57c90a3bfa0635c059e80e28837f949232d18b6282b0daf2a7ce52e89f57bae9e15a1b1ba5aa0297d062efb8b7c7088894338d881d1e85f05a5d816e528171eb7b35bf9e3a1cba0efad6c1e3281e194b9edc9f1a84320c2447e7430d80a7fae5609afd92def8a0aa0bd3bbfcbc16bafeb9cd927a91997211e25766279b4b814e71a5818e136b3bccca0af0d3259f5825ad310cfcd22ae937d90c3ce5406cb36e95c6f025b2060832956a0835ab70a2af6190d7b8dc6bc459313718c51655881e2e19b1159689b4316aed8a07c4cd0373084c75a605559c2aff67ac29c7cd7c692c32cd8cbe4f9a36958f80e80";
        proof[6] = hex"f901f180a0fccea1f26db665a20b37c5d2f73b434d21a0f60608c07dbf8b11e02aa50a4492a0a917273efd27e03b8d8d4f8cb0b6a778e2bf7b6db4c5a2f8911312aa91a2ddb6a0cdd3d9f285fc71145c8ebd505b79011c20baea19a29a0be7d64b13ec6d81699ba01c2cdcccb991030b8968998eec716f97f4af06bd36750e98e0ba7b067d4eabfda0e6c890389ff1ef930cbd0419c1a8bd72e6d35d436b667af09ee6a9f144fe9a81a09900df6984c2869d26319791db0d0ca5dd2fc1c38ca9523c55c744e165744166a0d5ff9d4c4c8f876c73ff1cbec51250f0e1f69c2e8b39c7839ff9c5bb07e093a1a09c24df6f8fc3fec0d631384fcc1e5b535ccc7ee4b13b29ed22ffb06ff01046eca0c128133187307978a18b035da3f6f9d7f90358adcea254a73f875d949542742aa005bb0906c66739cebb4cfe23401137793d35d6412b6b4c1334244f5baf4d2a4aa0c4257d84731e3c5fe8b8398033ab71e969f7a2b209014e3ae95abe7bebf3054ca0c386f38eea28915063f611860a9a5882f62780009831acb93e49e6e2ae535f3aa0a57059efa9f790109a455695d578e29bd0e21521936a9c63f4373f3aff0b3abea0ddb47e3acdbab07e9050a0fcdb42326a1d38d8c84524b012b5db03550ce70921a03b1d08887a4fa66a4dfbd69504f23c0d4f95837788596f86930e70199a2ca95780";
        proof[7] = hex"f89180a0ed355c65468bf8cb8b37fc687edb8f018ce01f8f0307895c200b8adc0d811385a087fc6085bcbaed54369663ee818ee34ad745b7b10f020941d49381c4fc5cbb2f8080a02eb1b6853b0cc8db1a43ad55fad3213a787f9ee79ef45784f88373d67f4be52b8080808080808080a01abe703862b44e7c9649eb9248f79f8dae9c0c15a2cb9b11b68eb26fb27e54898080";
        proof[8] = hex"f86e9d20c3547c60ee47f712d32e5acf38b35d1cc62e23b055a69bb88284c281b84ef84c46882b5932df3a443668a056e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421a0d8ef78646344da0ceb69cbcdb306939b3ba0514174fa28b6c6fa189953ff226d";
    }
}
