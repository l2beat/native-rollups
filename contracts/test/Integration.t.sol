// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/ForcedInboxValidated.sol";
import "./mocks/MockRollup.sol";

/// @notice Tiny exposure shim so the integration test can drive
///         `_extractAccountState` directly without going through `add`'s
///         signature recovery (which would require vitalik's private key).
///         Since `_extractAccountState` is now a pure verifier over
///         `(sender, stateRoot, proof)`, the wrapper bypasses the rollup
///         entirely; we still construct the inbox with a rollup address
///         only because the constructor requires it.
contract ExposedForcedInbox is ForcedInboxValidated {
    constructor(uint256 maxQueueSize, address rollup)
        ForcedInboxValidated(maxQueueSize, rollup)
    {}

    function extractAccountState(
        address sender,
        bytes32 stateRoot,
        bytes[] calldata accountProof
    ) external pure returns (uint64 nonce, uint256 balance) {
        return _extractAccountState(sender, stateRoot, accountProof);
    }
}

/// @notice End-to-end check that the vendored Optimism MPT verifier walks a
///         real-shape Ethereum mainnet account proof. The fixture below is
///         a snapshot of `eth_getProof` for vitalik.eth at a recent mainnet
///         block, captured against `$ETH_RPC_URL`. The expected fields are
///         the values that RPC returned, so the test is self-contained
///         against the captured root.
///
/// @dev To regenerate this fixture against any RPC:
///   ```
///   RPC=$ETH_RPC_URL
///   ADDR=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
///   BLOCK_JSON=$(cast block latest --rpc-url $RPC --json)
///   BLOCK_HEX=$(echo "$BLOCK_JSON" | jq -r .number)
///   STATEROOT=$(echo "$BLOCK_JSON" | jq -r .stateRoot)
///   cast rpc eth_getProof "$ADDR" "[]" "$BLOCK_HEX" --rpc-url $RPC > /tmp/proof.json
///   # then update the constants below from the response.
///   ```
contract IntegrationTest is Test {
    // --- captured fixture from $ETH_RPC_URL (https://rpc.slopo.net/...) ---
    address constant VITALIK = 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045;
    // L1 mainnet block 0x1814849 (25249865).
    bytes32 constant STATE_ROOT =
        0xdd27ed6acfed865a1b5c7d20416067d320f6e91ce3d370c2668dcbf2c63393c6;
    uint64 constant EXPECTED_NONCE = 5896;
    uint256 constant EXPECTED_BALANCE = 5682781186715981478;

    ExposedForcedInbox inbox;

    function setUp() public {
        // The inbox constructor still requires a rollup address even though
        // the proof-verifier path doesn't use it; pass a dummy.
        inbox = new ExposedForcedInbox(256, address(0x1));
    }

    /// Decode vitalik's real mainnet account proof and assert that
    /// `_extractAccountState` returns the fields `eth_getProof` reported.
    function test_extractsRealMainnetAccount() public view {
        bytes[] memory proof = _vitalikProof();
        (uint64 nonce, uint256 balance) =
            inbox.extractAccountState(VITALIK, STATE_ROOT, proof);
        assertEq(uint256(nonce), uint256(EXPECTED_NONCE), "nonce");
        assertEq(balance, EXPECTED_BALANCE, "balance");
    }

    /// Asking for a different address against this state root reverts via
    /// the MerkleTrie's path-remainder check, which is what we expect when
    /// the caller mis-routes a proof.
    function test_revertsMismatchedAccount() public {
        bytes[] memory proof = _vitalikProof();
        vm.expectRevert(); // exact error comes from the vendored MerkleTrie
        inbox.extractAccountState(address(0xdead), STATE_ROOT, proof);
    }

    // --- fixture ---

    /// @dev See the @dev block on the contract for the regen recipe.
    function _vitalikProof() internal pure returns (bytes[] memory proof) {
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
}
