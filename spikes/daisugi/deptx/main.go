// Command deptx sends EIP-8288 frame transactions to a daisugi node.
//
//	deptx deploy -initcode FILE
//	    deploys a contract with an ordinary transaction and prints its address.
//	deptx blob
//	    sends an ordinary EIP-4844 blob transaction with one blob, a control for
//	    whether the chain includes blobs at all.
//	deptx send -probe ADDR -witness FILE -data HASH
//	    sends a frame transaction with a VERIFY frame for the sender, a DEP_VERIFY
//	    frame declaring one leanSTARK dependency, and a SENDER frame calling
//	    DepProbe.probe(index of the dependency frame), in a mode-0 proof wrapper
//	    through eth_sendProofWrapper, then waits for its receipt. With -blob it
//	    also carries one blob, in the EIP-4844 (V0) network form a Prague chain
//	    requires.
//
// The witness file is the output of lean-bindings' `fixtures --prove-stark HASH`:
// the 32-byte verification key hash, then the proof.
package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/sha256"
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
	"github.com/ethereum/go-ethereum/crypto/kzg4844"
	"github.com/ethereum/go-ethereum/ethclient"
	"github.com/ethereum/go-ethereum/rlp"
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
	withBlob := flags.Bool("blob", false, "send: carry one blob")
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
	case "blob":
		sendBlob(ctx, client, key, chainID, *timeout)
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
		send(ctx, raw, client, key, chainID, common.HexToAddress(*probe), declared, blob[32:], *order, *withBlob, *timeout)
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
	probe common.Address, dep txtypes.FrameDependency, proof []byte, order string, withBlob bool, timeout time.Duration) {
	sender := crypto.PubkeyToAddress(key.PublicKey)
	nonce, err := client.PendingNonceAt(ctx, sender)
	check(err, "nonce")
	head, err := client.HeaderByNumber(ctx, nil)
	check(err, "head")

	// Without a dependency frame (order "vs"), the transaction is a control: it goes
	// through eth_sendRawTransaction, and the probe reads the VERIFY frame.
	depIndex := strings.IndexByte(order, 'd')
	if (order != "vs") && (len(order) != 3 || depIndex < 0 || !strings.Contains(order, "v") || !strings.Contains(order, "s")) {
		fail("order must be a permutation of vds, or vs")
	}
	probeIndex := max(depIndex, 0)
	calldata := append(crypto.Keccak256([]byte("probe(uint256)"))[:4], common.LeftPadBytes(big.NewInt(int64(probeIndex)).Bytes(), 32)...)
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
	fees := txtypes.FrameFees{GasTipCap: tip, GasFeeCap: feeCap}
	var sidecar *v0Sidecar
	if withBlob {
		sidecar = newSidecar()
		fees.BlobFeeCap = uint256.NewInt(1_000_000_000)
	}
	tx := txtypes.NewFrameTx(uint256.MustFromBig(chainID), sender, nonce, fees, frames, []*txtypes.FrameSignature{txtypes.SenderSignature()})
	if sidecar != nil {
		tx.BlobHashes = []common.Hash{sidecar.hash}
		fmt.Printf("blob %s\n", sidecar.hash)
	}
	check(tx.SignPayload(chainID, key), "sign")
	wrapped := txtypes.NewTx(tx)
	if depIndex < 0 {
		sendControl(ctx, raw, client, tx, wrapped, sidecar, probe, timeout)
		return
	}

	wrapper, err := txtypes.NewDirectProofWrapper([]*txtypes.Transaction{wrapped}, map[txtypes.FrameDependency][]byte{dep: proof})
	check(err, "wrapper")
	var encoded []byte
	if sidecar == nil {
		encoded, err = wrapper.MarshalBinary()
	} else {
		encoded, err = encodeV0Wrapper(tx, sidecar, wrapper)
	}
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

	report(ctx, raw, client, wrapped.Hash(), probe, start, timeout)
}

// sendControl sends the transaction without a wrapper, in its network form.
func sendControl(ctx context.Context, raw *rpc.Client, client *ethclient.Client, tx *txtypes.FrameTx, wrapped *txtypes.Transaction,
	s *v0Sidecar, probe common.Address, timeout time.Duration) {
	network, err := wrapped.MarshalBinary()
	check(err, "encode")
	if s != nil {
		network, err = encodeV0Tx(tx, s)
		check(err, "encode")
	}
	fmt.Printf("control tx %s without a dependency, %d bytes\n", wrapped.Hash(), len(network))
	start := time.Now()
	var hash common.Hash
	if err := raw.CallContext(ctx, &hash, "eth_sendRawTransaction", hexutil.Encode(network)); err != nil {
		fmt.Printf("eth_sendRawTransaction rejected it: %v\n", err)
		os.Exit(2)
	}
	report(ctx, raw, client, wrapped.Hash(), probe, start, timeout)
}

// report waits for the receipt and prints what the probe and the block show.
func report(ctx context.Context, raw *rpc.Client, client *ethclient.Client, hash common.Hash, probe common.Address, start time.Time, timeout time.Duration) {
	receipt := waitReceipt(ctx, client, hash, timeout)
	fmt.Printf("included in block %d after %s: status %d, gas used %d, blob gas used %d\n", receipt.BlockNumber, time.Since(start).Round(time.Second), receipt.Status, receipt.GasUsed, receipt.BlobGasUsed)
	for _, log := range receipt.Logs {
		if log.Address != probe || len(log.Data) != 8*32 {
			continue
		}
		word := func(i int) *big.Int { return new(big.Int).SetBytes(log.Data[i*32 : (i+1)*32]) }
		fmt.Printf("probe saw: mode %d, data length %d, has status %v, status %s, scheme %#x, data %s, key %s, blob %s\n",
			word(0), word(1), word(2).Sign() != 0, word(3), word(4), common.BytesToHash(log.Data[160:192]), common.BytesToHash(log.Data[192:224]),
			common.BytesToHash(log.Data[224:256]))
	}
	var block map[string]any
	check(raw.CallContext(ctx, &block, "eth_getBlockByNumber", hexutil.EncodeBig(receipt.BlockNumber), false), "block")
	fmt.Printf("block %d: starkProofHash %v, blockDepsHash %v, %d transactions\n", receipt.BlockNumber, block["starkProofHash"], block["blockDepsHash"], len(block["transactions"].([]any)))
	var full json.RawMessage
	check(raw.CallContext(ctx, &full, "eth_getTransactionReceipt", hash), "raw receipt")
	fmt.Printf("raw receipt: %s\n", full)
}

func sendBlob(ctx context.Context, client *ethclient.Client, key *ecdsa.PrivateKey, chainID *big.Int, timeout time.Duration) {
	from := crypto.PubkeyToAddress(key.PublicKey)
	nonce, err := client.PendingNonceAt(ctx, from)
	check(err, "nonce")
	head, err := client.HeaderByNumber(ctx, nil)
	check(err, "head")
	s := newSidecar()
	tip := uint256.NewInt(1_000_000_000)
	tx, err := types.SignNewTx(key, types.LatestSignerForChainID(chainID), &types.BlobTx{
		ChainID: uint256.MustFromBig(chainID), Nonce: nonce, GasTipCap: tip,
		GasFeeCap:  new(uint256.Int).Add(new(uint256.Int).Mul(uint256.MustFromBig(head.BaseFee), uint256.NewInt(2)), tip),
		Gas:        21_000, To: from, BlobFeeCap: uint256.NewInt(1_000_000_000), BlobHashes: []common.Hash{s.hash},
		Sidecar: types.NewBlobTxSidecar(types.BlobSidecarVersion0, []kzg4844.Blob{s.blob}, []kzg4844.Commitment{s.commitment}, []kzg4844.Proof{s.proof}),
	})
	check(err, "sign")
	check(client.SendTransaction(ctx, tx), "send")
	fmt.Printf("blob tx %s (blob %s) accepted by the pool\n", tx.Hash(), s.hash)
	receipt := waitReceipt(ctx, client, tx.Hash(), timeout)
	fmt.Printf("included in block %d: status %d, blob gas used %d\n", receipt.BlockNumber, receipt.Status, receipt.BlobGasUsed)
}

// v0Sidecar is one blob with its EIP-4844 commitment and proof.
type v0Sidecar struct {
	blob       kzg4844.Blob
	commitment kzg4844.Commitment
	proof      kzg4844.Proof
	hash       common.Hash
}

// newSidecar builds a blob of recognizable, canonical field elements.
func newSidecar() *v0Sidecar {
	s := &v0Sidecar{}
	for i := 0; i < len(s.blob); i += 32 {
		copy(s.blob[i+1:i+32], bytes.Repeat([]byte{0x5a}, 31))
	}
	var err error
	s.commitment, err = kzg4844.BlobToCommitment(&s.blob)
	check(err, "commitment")
	s.proof, err = kzg4844.ComputeBlobProof(&s.blob, s.commitment)
	check(err, "blob proof")
	s.hash = kzg4844.CalcBlobHashV1(sha256.New(), &s.commitment)
	return s
}

// encodeV0Wrapper encodes the direct wrapper as spamoor's MarshalBinary does, but with
// the transaction in the EIP-4844 network form, [payload, blobs, commitments, proofs]
// with no version: spamoor writes only the EIP-7594 form, which a Prague chain refuses.
func encodeV0Wrapper(tx *txtypes.FrameTx, s *v0Sidecar, w *txtypes.ProofWrapper) ([]byte, error) {
	network, err := encodeV0Tx(tx, s)
	if err != nil {
		return nil, err
	}
	deps := make([][]byte, 0, len(w.Dependencies))
	for _, dep := range w.Dependencies {
		deps = append(deps, txtypes.EncodeDependencies([]txtypes.FrameDependency{dep}))
	}
	content, err := rlp.EncodeToBytes([]any{deps, w.Proofs})
	if err != nil {
		return nil, err
	}
	return rlp.EncodeToBytes([]any{[][]byte{network}, w.Mode, rlp.RawValue(content)})
}

// encodeV0Tx returns the typed EIP-4844 network form of a blob-carrying frame transaction.
func encodeV0Tx(tx *txtypes.FrameTx, s *v0Sidecar) ([]byte, error) {
	var payload bytes.Buffer
	if err := tx.EncodePayload(&payload); err != nil {
		return nil, err
	}
	network, err := rlp.EncodeToBytes([]any{rlp.RawValue(payload.Bytes()), []kzg4844.Blob{s.blob},
		[]kzg4844.Commitment{s.commitment}, []kzg4844.Proof{s.proof}})
	if err != nil {
		return nil, err
	}
	return append([]byte{txtypes.FrameTxType}, network...), nil
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
