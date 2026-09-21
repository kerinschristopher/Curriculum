output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id

  # Consumers (EKS) shouldn't get these until the subnets can actually reach the internet
  depends_on = [
    aws_route_table_association.private,
    aws_route.private_nat,
  ]
}