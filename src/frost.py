# Copyright (c) 2021 Jesse Posner
# Distributed under the MIT software license, see the accompanying file LICENSE
# or http://www.opensource.org/licenses/mit-license.php.
#
# This code is currently a work in progress. It's not secure nor stable.  IT IS
# EXTREMELY DANGEROUS AND RECKLESS TO USE THIS MODULE IN PRODUCTION!
#
# This module implements Flexible Round-Optimized Schnorr Threshold Signatures
# (FROST) by Chelsea Komlo and Ian Goldberg
# (https://crysp.uwaterloo.ca/software/frost/).

"""Python FROST adaptor signatures implementation."""

import secrets
from hashlib import sha256

class FROST:
    class secp256k1:
        P = 2**256 - 2**32 - 977
        Q = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141
        G_x = 0x79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798
        G_y = 0x483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8

        @classmethod
        def G(cls):
            return FROST.Point(cls.G_x, cls.G_y)

    class Participant:
        """Class representing a FROST participant."""

        CONTEXT = b'FROST-BIP340'

        def __init__(self, index, threshold, participants):
            self.index = index
            self.threshold = threshold
            self.participants = participants
            self.coefficients = []
            self.coefficient_commitments = []
            self.proof_of_knowledge = []
            self.shares = []
            self.aggregate_share = None
            self.nonce_pairs = []
            # L_i
            self.nonce_commitment_pairs = []
            # Y
            self.public_key = None

        def init_keygen(self):
            Q = FROST.secp256k1.Q
            G = FROST.secp256k1.G()
            # 1. Generate polynomial with random coefficients, and with degree
            # equal to the threshold minus one.
            #
            # (a_i_0, . . ., a_i_(t - 1)) ⭠ $ ℤ_q
            self.coefficients = [secrets.randbits(256) % Q for _ in range(self.threshold)]
            # 2. Compute proof of knowledge of secret a_i_0.
            #
            # k ⭠ ℤ_q
            nonce = secrets.randbits(256) % Q
            # R_i = g^k
            nonce_commitment = nonce * G
            # i
            index_byte = self.index.to_bytes(1, 'big')
            # 𝚽
            context_bytes = self.CONTEXT
            # g^a_i_0
            secret = self.coefficients[0]
            secret_commitment = secret * G
            secret_commitment_bytes = secret_commitment.sec_serialize()
            # R_i
            nonce_commitment_bytes = nonce_commitment.sec_serialize()
            # c_i = H(i, 𝚽, g^a_i_0, R_i)
            challenge_hash = sha256()
            challenge_hash.update(index_byte)
            challenge_hash.update(context_bytes)
            challenge_hash.update(secret_commitment_bytes)
            challenge_hash.update(nonce_commitment_bytes)
            challenge_hash_bytes = challenge_hash.digest()
            challenge_hash_int = int.from_bytes(challenge_hash_bytes, 'big')
            # μ_i = k + a_i_0 * c_i
            s = (nonce + secret * challenge_hash_int) % Q
            # σ_i = (R_i, μ_i)
            self.proof_of_knowledge = [nonce_commitment, s]
            # 3. Compute coefficient commitments.
            #
            # C_i = ⟨𝜙_i_0, ..., 𝜙_i_(t - 1)⟩
            # 𝜙_i_j = g^a_i_j, 0 ≤ j ≤ t - 1
            self.coefficient_commitments = [coefficient * G for coefficient in self.coefficients]

        def verify_proof_of_knowledge(self, proof, secret_commitment, index):
            G = FROST.secp256k1.G()
            # l
            index_byte = index.to_bytes(1, 'big')
            # 𝚽
            context_bytes = self.CONTEXT
            # g^a_l_0
            secret_commitment_bytes = secret_commitment.sec_serialize()
            # R_l
            nonce_commitment = proof[0]
            nonce_commitment_bytes = nonce_commitment.sec_serialize()
            # c_l = H(l, 𝚽, g^a_l_0, R_l)
            challenge_input = index_byte + context_bytes + secret_commitment_bytes + nonce_commitment_bytes
            challenge_hash_bytes = sha256(challenge_input).digest()
            challenge_hash_int = int.from_bytes(challenge_hash_bytes, 'big')
            # μ_l
            s = proof[1]
            # R_l ≟ g^μ_l * 𝜙_l_0^-c_l, 1 ≤ l ≤ n, l ≠ i
            return nonce_commitment == (s * G) + (FROST.secp256k1.Q - challenge_hash_int) * secret_commitment

        def generate_shares(self):
            # (i, f_i(i)), (l, f_i(l))
            self.shares = [self.evaluate_polynomial(x) for x in range(1, self.participants + 1)]

        def evaluate_polynomial(self, x):
            # f_i(x) = ∑ a_i_j * x^j, 0 ≤ j ≤ t - 1
            # Horner's method
            y = 0
            for i in range(len(self.coefficients) - 1, -1, -1):
                y = y * x + self.coefficients[i]
            return y % FROST.secp256k1.Q

        def lagrange_coefficient(self, participant_indexes):
            Q = FROST.secp256k1.Q
            # λ_i = ∏ p_j/(p_j - p_i), 1 ≤ j ≤ α, j ≠ i
            numerator = 1
            denominator = 1
            for index in participant_indexes:
                if index == self.index:
                    continue
                numerator = numerator * index
                denominator = denominator * (index - self.index)
            return (numerator * pow(denominator, Q - 2, Q)) % Q

        def verify_share(self, y, coefficient_commitments):
            Q = FROST.secp256k1.Q
            G = FROST.secp256k1.G()
            # ∏ 𝜙_l_k^i^k mod q, 0 ≤ k ≤ t - 1
            expected_y_commitment = FROST.Point()
            for k in range(len(coefficient_commitments)):
                expected_y_commitment = expected_y_commitment + ((self.index ** k % Q) * coefficient_commitments[k])
            # g^f_l(i) ≟ ∏ 𝜙_l_k^i^k mod q, 0 ≤ k ≤ t - 1
            return y * G == expected_y_commitment

        def aggregate_shares(self, shares):
            # s_i = ∑ f_l(i), 1 ≤ l ≤ n
            aggregate_share = self.shares[self.index - 1]
            for share in shares:
                aggregate_share = aggregate_share + share
            self.aggregate_share = aggregate_share % FROST.secp256k1.Q

        def public_verification_share(self):
            G = FROST.secp256k1.G()
            # Y_i = g^s_i
            return self.aggregate_share * G

        def derive_public_key(self, secret_commitments):
            # Y = ∏ 𝜙_j_0, 1 ≤ j ≤ n
            public_key = self.coefficient_commitments[0]
            for secret_commitment in secret_commitments:
                public_key = public_key + secret_commitment
            self.public_key = public_key
            return public_key

        def generate_nonces(self, amount):
            Q = FROST.secp256k1.Q
            G = FROST.secp256k1.G()

            # Preprocess(π) ⭢  (i, ⟨(D_i_j, E_i_j)⟩), 1 ≤ j ≤ π
            for _ in range(amount):
                # (d_i_j, e_i_j) ⭠ $ ℤ*_q x ℤ*_q
                nonce_pair = [secrets.randbits(256) % Q, secrets.randbits(256) % Q]
                # (D_i_j, E_i_j) = (g^d_i_j, g^e_i_j)
                nonce_commitment_pair = [nonce_pair[0] * G, nonce_pair[1] * G]

                self.nonce_pairs.append(nonce_pair)
                self.nonce_commitment_pairs.append(nonce_commitment_pair)

        def sign(self, message, nonce_commitment_pairs, participant_indexes):
            # R
            group_commitment = FROST.Aggregator.group_commitment(message, nonce_commitment_pairs, participant_indexes)

            # c = H_2(R, Y, m)
            challenge_hash = FROST.Aggregator.challenge_hash(group_commitment, self.public_key, message)

            # Fetch next available nonce pair
            nonce_pair = self.nonce_pairs.pop()
            # d_i
            first_nonce = nonce_pair[0]
            # e_i
            second_nonce = nonce_pair[1]
            # Negate d_i and e_i if R is odd
            if group_commitment.y % 2 != 0:
                first_nonce = FROST.secp256k1.Q - first_nonce
                second_nonce = FROST.secp256k1.Q - second_nonce
            # p_i = H_1(i, m, B), i ∈ S
            binding_value = FROST.Aggregator.binding_value(self.index, message, nonce_commitment_pairs, participant_indexes)
            # λ_i
            lagrange_coefficient = self.lagrange_coefficient(participant_indexes)
            # s_i
            aggregate_share = self.aggregate_share
            # Negate s_i if Y is odd
            if self.public_key.y % 2 != 0:
                aggregate_share = FROST.secp256k1.Q - aggregate_share

            # z_i = d_i + (e_i * p_i) + λ_i * s_i * c
            return (first_nonce + (second_nonce * binding_value) + lagrange_coefficient * aggregate_share * challenge_hash) % FROST.secp256k1.Q

    class Aggregator:
        """Class representing the signature aggregator."""

        def __init__(self, public_key, message, nonce_commitment_pair_list, participant_indexes):
            # Y
            self.public_key = public_key
            # m
            self.message = message
            # L
            self.nonce_commitment_pair_list = nonce_commitment_pair_list
            # S = α: t ≤ α ≤ n
            self.participant_indexes = participant_indexes
            # B
            self.nonce_commitment_pairs = []

        @classmethod
        def group_commitment(self, message, nonce_commitment_pairs, participant_indexes):
            # R
            group_commitment = FROST.Point()
            for index in participant_indexes:
                # p_l = H_1(l, m, B), l ∈ S
                binding_value = self.binding_value(index, message, nonce_commitment_pairs, participant_indexes)
                # D_l
                first_commitment = nonce_commitment_pairs[index-1][0]
                # E_l
                second_commitment = nonce_commitment_pairs[index-1][1]
                # R = ∏ D_l * (E_l)^p_l, l ∈ S
                group_commitment = group_commitment + (first_commitment + (binding_value * second_commitment))
            return group_commitment

        @classmethod
        def binding_value(self, index, message, nonce_commitment_pairs, participant_indexes):
            binding_value = sha256()
            # l
            index_byte = index.to_bytes(1, 'big')
            # B
            nonce_commitment_pairs_bytes = []
            for index in participant_indexes:
                participant_pair = nonce_commitment_pairs[index-1]
                participant_pair_bytes = b''.join([commitment.sec_serialize() for commitment in participant_pair])
                nonce_commitment_pairs_bytes.append(participant_pair_bytes)
            nonce_commitment_pairs_bytes = b''.join(nonce_commitment_pairs_bytes)
            # p_l = H_1(l, m, B), l ∈ S
            binding_value = sha256()
            binding_value.update(index_byte)
            binding_value.update(message)
            binding_value.update(nonce_commitment_pairs_bytes)
            binding_value_bytes = binding_value.digest()

            return int.from_bytes(binding_value_bytes, 'big')

        @classmethod
        def challenge_hash(self, nonce_commitment, public_key, message):
            # c = H_2(R, Y, m)
            tag_hash = sha256(b'BIP0340/challenge').digest()
            challenge_hash = sha256()
            challenge_hash.update(tag_hash)
            challenge_hash.update(tag_hash)
            challenge_hash.update(nonce_commitment.xonly_serialize())
            challenge_hash.update(public_key.xonly_serialize())
            challenge_hash.update(message)
            challenge_hash_bytes = challenge_hash.digest()

            return int.from_bytes(challenge_hash_bytes, 'big') % FROST.secp256k1.Q

        def signing_inputs(self):
            # B = ⟨(i, D_i, E_i)⟩_i∈S
            nonce_commitment_pairs = [None] * max(self.participant_indexes)
            # P_i ∈ S
            for index in self.participant_indexes:
                # L_i
                participant_pairs = self.nonce_commitment_pair_list[index-1]
                # Fetch next available commitment
                nonce_commitment_pairs[index-1] = participant_pairs.pop()
            self.nonce_commitment_pairs = nonce_commitment_pairs
            # (m, B)
            return [self.message, nonce_commitment_pairs]

        def signature(self, signature_shares):
            # R
            group_commitment = self.group_commitment(self.message, self.nonce_commitment_pairs, self.participant_indexes)
            # c = H_2(R, Y, m)
            challenge_hash = self.challenge_hash(group_commitment, self.public_key, self.message)
            # TODO: verify each signature share
            # σ = (R, z)
            nonce_commitment = group_commitment.xonly_serialize()
            z = (
                sum(signature_shares) % FROST.secp256k1.Q
            ).to_bytes(32, 'big')

            return (nonce_commitment + z).hex()

    class Point:
        """Class representing an elliptic curve point."""

        def __init__(self, x=float('inf'), y=float('inf')):
            self.x = x
            self.y = y

        @classmethod
        def sec_deserialize(cls, hex_public_key):
            P = FROST.secp256k1.P
            hex_bytes = bytes.fromhex(hex_public_key)
            is_even = hex_bytes[0] == 2
            x_bytes = hex_bytes[1:]
            x = int.from_bytes(x_bytes, 'big')
            y_squared = (pow(x, 3, P) + 7) % P
            y = pow(y_squared, (P + 1) // 4, P)

            if y % 2 == 0:
                even_y = y
                odd_y = (P - y) % P
            else:
                even_y = (P - y) % P
                odd_y = y
            y = even_y if is_even else odd_y

            return cls(x, y)

        def sec_serialize(self):
            prefix = b'\x02' if self.y % 2 == 0 else b'\x03'

            return prefix + self.x.to_bytes(32, 'big')

        @classmethod
        def xonly_deserialize(cls, hex_public_key):
            P = FROST.secp256k1.P
            hex_bytes = bytes.fromhex(hex_public_key)
            x = int.from_bytes(hex_bytes, 'big')
            y_squared = (pow(x, 3, P) + 7) % P
            y = pow(y_squared, (P + 1) // 4, P)

            if y % 2 != 0:
                y = (P - y) % P

            return cls(x, y)

        def xonly_serialize(self):
            return self.x.to_bytes(32, 'big')

        # point at infinity
        def is_zero(self):
            return self.x == float('inf') or self.y == float('inf')

        def __eq__(self, other):
            return self.x == other.x and self.y == other.y

        def __ne__(self, other):
            return not self == other

        def __neg__(self):
            P = FROST.secp256k1.P
            if self.is_zero():
                return self

            return self.__class__(self.x, P - self.y)

        def dbl(self):
            x = self.x
            y = self.y
            P = FROST.secp256k1.P
            s = (3 * x * x * pow(2 * y, P - 2, P)) % P
            sum_x = (s * s - 2 * x) % P
            sum_y = (s * (x - sum_x) - y) % P

            return self.__class__(sum_x, sum_y)

        def __add__(self, other):
            P = FROST.secp256k1.P

            if self == other:
                return self.dbl()
            if self.is_zero():
                return other
            if other.is_zero():
                return self
            if self.x == other.x and self.y != other.y:
                return self.__class__()
            s = ((other.y - self.y) * pow(other.x - self.x, P - 2, P)) % P
            sum_x = (s * s - self.x - other.x) % P
            sum_y = (s * (self.x - sum_x) - self.y) % P

            return self.__class__(sum_x, sum_y)

        def __rmul__(self, scalar):
            p = self
            r = self.__class__()
            i = 1

            while i <= scalar:
                if i & scalar:
                    r = r + p
                p = p.dbl()
                i <<= 1

            return r

        def __str__(self):
            if self.is_zero():
                return '0'
            return 'X: 0x{:x}\nY: 0x{:x}'.format(self.x, self.y)

        def __repr__(self) -> str:
            return self.__str__()
