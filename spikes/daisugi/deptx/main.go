// Command deptx sends EIP-8288 frame transactions to a daisugi node.
//
//	deptx deploy -initcode FILE
//	    deploys a contract with an ordinary transaction and prints its address.
//	deptx send -probe ADDR -witness FILE -data HASH
//	    sends a frame transaction with a VERIFY frame for the sender, a DEP_VERIFY
//	    frame declaring one leanSTARK dependency, and a SENDER frame calling
//	    DepProbe.probe(index of the dependency frame), in a mode-0 proof wrapper
//	    through eth_sendProofWrapper, then waits for its receipt.
//
// The witness file is the output of lean-bindings' `fixtures --prove-stark HASH`:
// the 32-byte verification key hash, then the proof.
package main

import (
	"context"
	"crypto/ecdsa"
	"encoding/json"
	"flag"
	"fmt"
	"math/big"
	"os"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
	"github.com/ethereum/go-ethereum/rpc"
	"github.com/ethpandaops/spamoor/txtypes"
	"github.com/holiman/uint256"
)

// The first prefunded account of ethereum-package's default mnemonic.
const defaultKey = "bcdf20249abf0ed6d944c0288fad489e33f66b3960d9e6229c1cd214ed3bbe31"

func main() {
	if len(os.Args) < 2 {
		fail("usage: deptx deploy|send [flags]")
	}
	flags := flag.NewFlagSet(os.Args[1], flag.ExitOnError)
	rpcURL := flags.String("rpc", "", "execution RPC URL")
	keyHex := flags.String("key", defaultKey, "sender private key")
	initcode := flags.String("initcode", "", "deploy: file with the hex initcode")
	probe := flags.String("probe", "", "send: DepProbe address")
	witnessFile := flags.String("witness", "", "send: key || proof file from fixtures --prove-stark")
	data := flags.String("data", "", "send: the hash the proof is over")
	declareData := flags.String("declare-data", "", "send: declare this data hash instead (negative test)")
	declareKey := flags.String("declare-key", "", "send: declare this verification key instead (negative test)")
	order := flags.String("order", "vds", "send: frame order, v = VERIFY, d = DEP_VERIFY, s = SENDER")
	timeout := flags.Duration("timeout", 3*time.Minute, "how long to wait for the receipt")
	flags.Parse(os.Args[2:])

	ctx := context.Background()
	raw, err := rpc.Dial(*rpcURL)
	check(err, "dial")
	client := ethclient.NewClient(raw)
	key, err := crypto.HexToECDSA(strings.TrimPrefix(*keyHex, "0x"))
	check(err, "key")
	chainID, err := client.ChainID(ctx)
	check(err, "chain id")

	switch os.Args[1] {
	case "deploy":
		code, err := os.ReadFile(*initcode)
		check(err, "initcode")
		deploy(ctx, client, key, chainID, common.FromHex(strings.TrimSpace(string(code))), *timeout)
	case "send":
		blob, err := os.ReadFile(*witnessFile)
		check(err, "witness")
		if len(blob) <= 32 {
			fail("witness file too short")
		}
		proven := txtypes.FrameDependency{
			Scheme:          txtypes.DepSchemeLeanStark,
			DataHash:        common.HexToHash(*data),
			VerificationKey: common.BytesToHash(blob[:32]),
		}
		declared := proven
		if *declareData != "" {
			declared.DataHash = common.HexToHash(*declareData)
		}
		if *declareKey != "" {
			declared.VerificationKey = common.HexToHash(*declareKey)
		}
		send(ctx, raw, client, key, chainID, common.HexToAddress(*probe), declared, blob[32:], *order, *timeout)
	default:
		fail("unknown command " + os.Args[1])
	}
}

func deploy(ctx context.Context, client *ethclient.Client, key *ecdsa.PrivateKey, chainID *big.Int, code []byte, timeout time.Duration) {
	from := crypto.PubkeyToAddress(key.PublicKey)
	nonce, err := client.PendingNonceAt(ctx, from)
	check(err, "nonce")
	tip := big.NewInt(1_000_000_000)
	head, err := client.HeaderByNumber(ctx, nil)
	check(err, "head")
	feeCap := new(big.Int).Add(new(big.Int).Mul(head.BaseFee, big.NewInt(2)), tip)
	tx, err := types.SignNewTx(key, types.LatestSignerForChainID(chainID), &types.DynamicFeeTx{
		ChainID: chainID, Nonce: nonce, GasTipCap: tip, GasFeeCap: feeCap, Gas: 3_000_000, Data: code,
	})
	check(err, "sign")
	check(client.SendTransaction(ctx, tx), "send")
	receipt := waitReceipt(ctx, client, tx.Hash(), timeout)
	fmt.Printf("deployed %s in block %d, status %d, gas %d\n", receipt.ContractAddress, receipt.BlockNumber, receipt.Status, receipt.GasUsed)
}

func send(ctx context.Context, raw *rpc.Client, client *ethclient.Client, key *ecdsa.PrivateKey, chainID *big.Int,
	probe common.Address, dep txtypes.FrameDependency, proof []byte, order string, timeout time.Duration) {
	sender := crypto.PubkeyToAddress(key.PublicKey)
	nonce, err := client.PendingNonceAt(ctx, sender)
	check(err, "nonce")
	head, err := client.HeaderByNumber(ctx, nil)
	check(err, "head")

	depIndex := strings.IndexByte(order, 'd')
	if depIndex < 0 || len(order) != 3 || !strings.Contains(order, "v") || !strings.Contains(order, "s") {
		fail("order must be a permutation of vds")
	}
	calldata := append(crypto.Keccak256([]byte("probe(uint256)"))[:4], common.LeftPadBytes(big.NewInt(int64(depIndex)).Bytes(), 32)...)
	frames := make([]*txtypes.Frame, 0, 3)
	for _, c := range order {
		switch c {
		case 'v':
			frames = append(frames, txtypes.SelfVerifyFrame(txtypes.DefaultCodeVerifyLimits(true)))
		case 'd':
			frames = append(frames, txtypes.DependencyFrame(dep))
		case 's':
			frames = append(frames, txtypes.UserOpFrame(&probe, nil, calldata, txtypes.FrameLimits{Execution: 300_000}))
		}
	}
	tip := uint256.NewInt(1_000_000_000)
	feeCap := new(uint256.Int).Add(new(uint256.Int).Mul(uint256.MustFromBig(head.BaseFee), uint256.NewInt(2)), tip)
	tx := txtypes.NewFrameTx(uint256.MustFromBig(chainID), sender, nonce,
		txtypes.FrameFees{GasTipCap: tip, GasFeeCap: feeCap}, frames, []*txtypes.FrameSignature{txtypes.SenderSignature()})
	check(tx.SignPayload(chainID, key), "sign")
	wrapped := txtypes.NewTx(tx)

	wrapper, err := txtypes.NewDirectProofWrapper([]*txtypes.Transaction{wrapped}, map[txtypes.FrameDependency][]byte{dep: proof})
	check(err, "wrapper")
	encoded, err := wrapper.MarshalBinary()
	check(err, "encode wrapper")
	fmt.Printf("tx %s: frames %s, dependency frame %d, declares (%s, %s, %s)\n", wrapped.Hash(), order, depIndex, dep.Scheme, dep.DataHash, dep.VerificationKey)
	fmt.Printf("wrapper %d bytes, proof %d bytes\n", len(encoded), len(proof))

	start := time.Now()
	var hashes []common.Hash
	if err := raw.CallContext(ctx, &hashes, "eth_sendProofWrapper", hexutil.Encode(encoded)); err != nil {
		fmt.Printf("eth_sendProofWrapper rejected it after %s: %v\n", time.Since(start).Round(time.Millisecond), err)
		os.Exit(2)
	}
	fmt.Printf("eth_sendProofWrapper accepted %v in %s\n", hashes, time.Since(start).Round(time.Millisecond))

	receipt := waitReceipt(ctx, client, wrapped.Hash(), timeout)
	fmt.Printf("included in block %d after %s: status %d, gas used %d\n", receipt.BlockNumber, time.Since(start).Round(time.Second), receipt.Status, receipt.GasUsed)
	for _, log := range receipt.Logs {
		if log.Address != probe || len(log.Data) != 7*32 {
			continue
		}
		word := func(i int) *big.Int { return new(big.Int).SetBytes(log.Data[i*32 : (i+1)*32]) }
		fmt.Printf("probe saw: mode %d, data length %d, has status %v, status %s, scheme %#x, data %s, key %s\n",
			word(0), word(1), word(2).Sign() != 0, word(3), word(4), common.BytesToHash(log.Data[160:192]), common.BytesToHash(log.Data[192:224]))
	}
	var block map[string]any
	check(raw.CallContext(ctx, &block, "eth_getBlockByNumber", hexutil.EncodeBig(receipt.BlockNumber), false), "block")
	fmt.Printf("block %d: starkProofHash %v, blockDepsHash %v, %d transactions\n", receipt.BlockNumber, block["starkProofHash"], block["blockDepsHash"], len(block["transactions"].([]any)))
	var full json.RawMessage
	check(raw.CallContext(ctx, &full, "eth_getTransactionReceipt", wrapped.Hash()), "raw receipt")
	fmt.Printf("raw receipt: %s\n", full)
}

func waitReceipt(ctx context.Context, client *ethclient.Client, hash common.Hash, timeout time.Duration) *types.Receipt {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		receipt, err := client.TransactionReceipt(ctx, hash)
		if err == nil {
			return receipt
		}
		time.Sleep(2 * time.Second)
	}
	fail(fmt.Sprintf("no receipt for %s after %s", hash, timeout))
	return nil
}

func check(err error, what string) {
	if err != nil {
		fail(what + ": " + err.Error())
	}
}

func fail(message string) {
	fmt.Fprintln(os.Stderr, message)
	os.Exit(1)
}
