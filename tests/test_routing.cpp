#include <iostream>
#include <string>
#include <cassert>
#include "../cluster/routing.hpp"

using namespace nanodb::cluster;

void test_fnv1a_64_deterministic() {
    std::string key1 = "test_key_1";
    std::string key2 = "test_key_2";

    uint64_t hash1_a = fnv1a_64(key1);
    uint64_t hash1_b = fnv1a_64(key1);
    assert(hash1_a == hash1_b);

    uint64_t hash2 = fnv1a_64(key2);
    assert(hash1_a != hash2);
    std::cout << "test_fnv1a_64_deterministic passed.\n";
}

int main() {
    test_fnv1a_64_deterministic();
    std::cout << "All routing tests passed.\n";
    return 0;
}
