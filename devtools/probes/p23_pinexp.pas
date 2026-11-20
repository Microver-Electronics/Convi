function Pt(X, Y: Double): TLocation;
begin
  Result := Point(MilsToCoord(X), MilsToCoord(Y));
end;

procedure MakeCase(S: Variant; Des: String; X, Y: Double; WireAtLoc: Boolean);
var C, P, W, PP: Variant; WX: Double;
begin
  C := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
  C.LibReference := 'T'; C.CurrentPartID := 1; C.DisplayMode := 0;
  C.Location := Pt(X, Y);
  S.RegisterSchObjectInContainer(C);
  C.Designator.Text := Des;
  P := SchServer.SchObjectFactory(ePin, eCreate_Default);
  P.Location := Pt(X, Y); P.Orientation := eRotate0; P.PinLength := MilsToCoord(100);
  P.Designator := '1'; P.Name := 'A'; P.OwnerPartId := 1; P.Electrical := eElectricPassive;
  C.AddSchObject(P);
  W := SchServer.SchObjectFactory(eWire, eCreate_Default);
  if WireAtLoc then WX := X else WX := X + 100;
  W.Location := Pt(WX, Y);
  W.InsertVertex := 1; W.SetState_Vertex(1, Pt(WX, Y));
  W.InsertVertex := 2; W.SetState_Vertex(2, Pt(WX, Y + 200));
  S.RegisterSchObjectInContainer(W);
  PP := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
  PP.Location := Pt(WX, Y + 200); PP.Text := 'PWR_' + Des; PP.Style := ePowerBar; PP.Orientation := eRotate90;
  S.RegisterSchObjectInContainer(PP);
end;

procedure Body;
var Doc, S: Variant;
begin
  Doc := CreateNewDocumentFromDocumentKind('SCH');
  S := SchServer.GetCurrentSchDocument;
  MakeCase(S, 'A1', 1000, 1000, True);
  MakeCase(S, 'A2', 2000, 1000, False);
  MakeCase(S, 'B1', 3000, 1000.717, True);
  MakeCase(S, 'B2', 4000, 1000.717, False);
  Doc.SetFileName('%OUT%\pinexp\pinexp.SchDoc');
  Doc.DoFileSave('');
  Client.CloseDocument(Doc);
  L('saved');
end;
