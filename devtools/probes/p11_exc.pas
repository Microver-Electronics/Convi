procedure Body;
var X: Variant; SL: TStringList; A: Variant; i: Integer;
begin
  try
    X := Nil;
    X.Foo := 1;
  except
    L('caught: ' + ExceptionMessage);
  end;
  L('decimal sep: [' + DecimalSeparator + ']');
  SL := TStringList.Create;
  SL.Delimiter := #9;
  SL.StrictDelimiter := True;
  SL.QuoteChar := #1;
  SL.DelimitedText := 'CMD' + #9 + 'a=1' + #9 + 'b=x"y z' + #9 + 'c=';
  L('count=' + IntToStr(SL.Count) + ' a=' + SL.Values['a'] + ' b=' + SL.Values['b'] + ' c=[' + SL.Values['c'] + '] missing=[' + SL.Values['zz'] + ']');
  A := VarArrayCreate([0, 10], varVariant);
  A[3] := PCBServer;
  L('vararray ok: ' + BoolToStr(A[3] <> Nil, True));
  L('eMechanical17=' + IntToStr(eMechanical17));
end;
