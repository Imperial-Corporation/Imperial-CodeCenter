// Zig : allocation explicite avec un allocateur général et libération différée (defer).
const std = @import("std");

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var numbers = std.ArrayList(u32).init(allocator);
    defer numbers.deinit();

    var i: u32 = 1;
    while (i <= 5) : (i += 1) {
        try numbers.append(i * i);
    }

    var total: u32 = 0;
    for (numbers.items) |n| total += n;

    std.debug.print("carrés : {any}, somme = {d}\n", .{ numbers.items, total });
}
